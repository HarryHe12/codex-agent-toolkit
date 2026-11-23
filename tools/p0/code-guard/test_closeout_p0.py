"""Check the scopes and recovery paths that previously stalled tasks."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory, gettempdir
import unittest
from unittest.mock import patch

import closeout
import hook


class CloseoutTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve()
        self.root = self.home / 'project'
        self.root.mkdir()
        patched = patch.object(hook, 'STATE', self.home / 'state')
        patched.start()
        self.addCleanup(patched.stop)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Toolkit Fixture')
        self.git('config', 'user.email', 'fixture@example.test')
        self.commit('value = 1\n')
        self.record = closeout.registration('fixture', self.root)
        self.event = {'session_id': 'fixture', 'hook_event_name': 'Stop', 'cwd': str(self.root)}

    def git(self, *args, root=None):
        return closeout.git(root or self.root, *args)

    def commit(self, contents, root=None):
        root = root or self.root
        (root / 'app.py').write_text(contents)
        self.git('add', 'app.py', root=root)
        self.git('commit', '-qm', 'Fixture change', root=root)

    def turn(self, command, previous=None):
        path = self.home / 'turn.jsonl'
        self.event['transcript_path'] = str(path)
        rows = []
        for turn_id, script in (('previous', previous), ('current', command)):
            if script is None:
                continue
            rows.extend([
                {'type': 'turn_context', 'payload': {'turn_id': turn_id}},
                {'type': 'event_msg', 'payload': {'turn_id': turn_id, 'item': {
                    'type': 'CommandExecution', 'cwd': self.root.as_uri(),
                    'command': ['/bin/sh', '-lc', script], 'status': 'completed'}}},
            ])
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def test_read_only_task_ignores_a_preexisting_dirty_tree(self):
        (self.root / 'somebody-elses-work.py').write_text('def ???\n')
        self.turn('git status --short', previous='touch old.py')
        with patch.object(hook, 'run_scan', side_effect=AssertionError('unexpected scan')):
            self.assertEqual(closeout.stop(self.event), {})

    def test_unregistered_write_is_reported_without_scanning(self):
        (self.root / 'app.py').write_text('value = 2\n')
        self.turn("printf 'value = 2\\n' > app.py")
        with patch.object(hook, 'run_scan', side_effect=AssertionError('unexpected scan')):
            result = closeout.stop(self.event)
        self.assertIn('without a pre-edit baseline', result.get('systemMessage', ''), result)
        self.assertFalse(self.record.exists())
        self.assertEqual((self.root / 'app.py').read_text(), 'value = 2\n')

    def test_tmp_does_not_become_a_project(self):
        shared = Path(gettempdir()).resolve()
        with patch.object(Path, 'cwd', return_value=shared), patch.object(hook, 'root_for', return_value=shared):
            with patch.object(sys, 'argv', ['closeout.py', 'begin']), patch.dict(os.environ, CODEX_SESSION_ID='fixture'):
                with patch.object(closeout, 'begin', side_effect=AssertionError('unexpected shared-root scan')):
                    with self.assertRaisesRegex(ValueError, 'specific Git worktree'):
                        closeout.main()
        self.assertEqual(closeout.target_root(self.root), self.root)
        self.assertEqual(closeout.target_root(self.home, standalone=True), self.home)

    def incoming_change(self):
        remote = self.home / 'remote.git'
        other = self.home / 'remote-author'
        self.git('clone', '-q', '--bare', '.', os.fspath(remote))
        self.git('remote', 'add', 'origin', os.fspath(remote))
        self.git('fetch', '-q', 'origin')
        self.git('branch', '--set-upstream-to=origin/main')
        closeout.begin(self.root, self.record)
        self.git('clone', '-q', os.fspath(remote), os.fspath(other))
        self.git('config', 'user.name', 'Fixture Remote Author', root=other)
        self.git('config', 'user.email', 'remote@example.test', root=other)
        self.commit('def ???\n', other)
        self.git('push', '-q', 'origin', 'main', root=other)
        self.git('pull', '--ff-only', '-q')
        self.turn('git pull --ff-only')

    def test_incoming_diagnostics_do_not_block_fast_forward(self):
        self.incoming_change()
        result = closeout.stop(self.event)
        self.assertNotIn('decision', result)
        self.assertIn('clean fast-forward sync', result['systemMessage'])
        self.assertIn('app.py', result['systemMessage'])
        self.assertFalse(self.record.exists())

    def test_local_work_blocks_sync_fast_path(self):
        self.incoming_change()
        (self.root / 'local.py').write_text('def ???\n')
        self.assertEqual(closeout.stop(self.event)['decision'], 'block')
        self.assertTrue(self.record.is_file())

    def test_same_registration_recovers_after_missing_dependency(self):
        closeout.begin(self.root, self.record)
        (self.root / 'app.py').write_text('value = 2\n')
        (self.root / 'README.md').write_text('The configured value is 2.\n')
        command = [sys.executable, '-c', 'from app import value; assert value == 2']
        missing = self.home / 'missing-guard'
        missing.mkdir()
        (missing / 'package.json').write_text('{"dependencies":{"eslint":"*"}}')
        with patch.object(hook, 'BASE', missing):
            result = closeout.stop(self.event)
            self.assertIn('Missing code guard package(s): eslint', result['reason'])
            repeated = closeout.stop(dict(self.event, stop_hook_active=True))
            self.assertFalse(repeated['continue'])
        closeout.verify(self.root, self.record, 'Value should now be 2.', command, False)
        self.assertEqual(closeout.stop(self.event), {})

    def test_stop_rejects_verified_files_changed_afterwards(self):
        closeout.begin(self.root, self.record)
        doc = self.root / 'README.md'
        doc.write_text('Before verification.\n')
        closeout.verify(self.root, self.record, 'Reviewed text.', [], True)
        doc.write_text('Modified after verification.\n')
        result = closeout.stop(self.event)
        self.assertEqual(result['decision'], 'block')
        self.assertIn('closeout.py verify', result['reason'])
        closeout.verify(self.root, self.record, 'Reviewed the updated text.', [], True)
        self.assertEqual(closeout.stop(self.event), {})

    def test_scan_timeout_identifies_the_stage_and_preserves_scope(self):
        failure = subprocess.TimeoutExpired('scan', timeout=90)
        with patch.object(hook, 'run_scan', side_effect=failure):
            with self.assertRaisesRegex(ValueError, f'pre-edit scan timed out.*{self.root}'):
                closeout.begin(self.root, self.record)
        self.assertFalse(self.record.exists())


if __name__ == '__main__':
    unittest.main()
