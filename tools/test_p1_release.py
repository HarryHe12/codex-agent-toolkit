"""Exercise P1 installation, host adapters and the CI guard from a rebuilt release."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest.mock import patch

from build_p0_release import export
from build_p1_release import ARCHIVE_NAME, RELEASE, build, collect


def run(*args, cwd=None, input_text=None, extra_env=None):
    env = dict(os.environ)
    env.pop('CODEX_GUARD_NODE', None)
    env.update(extra_env or {})
    return subprocess.run(list(map(str, args)), cwd=cwd, env=env, input=input_text,
                          text=True, capture_output=True, timeout=200)


def ok(result):
    if result.returncode:
        raise AssertionError(result.stdout + '\n' + result.stderr)
    return result.stdout


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory(prefix='toolkit P1 release ')
        cls.addClassCleanup(directory.cleanup)
        cls.root = Path(directory.name).resolve()
        entries = collect()
        archive = build(entries, cls.root / 'archive')
        exported = cls.root / 'public'
        export(entries, exported)
        rebuilt = RELEASE / ARCHIVE_NAME
        if rebuilt.exists() and archive.read_bytes() != rebuilt.read_bytes():
            raise AssertionError('Checked-in P1 archive differs from a rebuild.')
        cls.source = cls.root / 'unpacked'
        import zipfile
        with zipfile.ZipFile(archive) as package:
            package.extractall(cls.source)
        if any((cls.source / name).read_bytes() != (exported / name).read_bytes() for name in entries):
            raise AssertionError('The public export differs from the unpacked release.')
        cls.install = cls.source / 'install.py'
        cls.home = cls.root / 'managed install'
        cls.original_config = (b'{"otherSetting":123,"hooks":{"Stop":[{"hooks":'
                               b'[{"type":"command","command":"unrelated script"}]}]}}\n')

    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(directory.cleanup)
        self.scenario = Path(directory.name)

    def install_cli(self, action, *args, home=None):
        return run(sys.executable, self.install, action, '--home', home or self.home, '--offline', *args)

    def installed(self, host='manual', config=None):
        args = ['--host', host]
        if config is not None:
            args.extend(('--config', config))
        return json.loads(ok(self.install_cli('install', *args)))

    def repo(self):
        repo = self.scenario / 'project source'
        repo.mkdir()
        self.git(repo, 'init', '-qb', 'main')
        (repo / 'app.js').write_text('console.log(1);\n')
        self.commit(repo)
        return repo

    def git(self, repo, *args):
        return ok(run('git', '-C', repo, *args)).strip()

    def commit(self, repo):
        self.git(repo, 'add', '.')
        self.git(repo, '-c', 'user.name=Toolkit Test', '-c', 'user.email=fixture@example.test',
                 'commit', '-qm', 'Fixture change')
        return self.git(repo, 'rev-parse', 'HEAD')

    def test_preview_interrupted_npm_restore_and_idempotency(self):
        home = self.scenario / 'empty home'
        config = self.scenario / 'hooks.json'
        config.write_bytes(self.original_config)
        options = ['--host', 'codex', '--config', config]
        existing_cache = set(self.source.rglob('__pycache__'))
        preview = json.loads(ok(self.install_cli('plan', *options, home=home)))
        self.assertTrue(preview['would_change_settings'])
        self.assertFalse(home.exists())
        self.assertEqual(set(self.source.rglob('__pycache__')), existing_cache)
        bad = self.scenario / 'failing npm'
        bad.write_text('#!/bin/sh\nexit 71\n')
        bad.chmod(0o700)
        attempt = self.install_cli('install', *options, '--npm', bad, home=home)
        self.assertEqual(2, attempt.returncode)
        self.assertEqual(config.read_bytes(), self.original_config)
        self.assertEqual(list((home / 'versions').iterdir()), [])
        ok(self.install_cli('install', *options, home=home))
        actual = config.read_bytes()
        self.assertIn(b'unrelated script', actual)
        self.assertEqual(actual.count(b'guard.py'), 1)
        ok(self.install_cli('install', *options, home=home))
        self.assertEqual(config.read_bytes(), actual)
        ok(self.install_cli('verify', *options, home=home))
        config.write_bytes(actual + b'\n')
        refusal = self.install_cli('rollback', *options, home=home)
        self.assertEqual(refusal.returncode, 2)
        self.assertEqual(config.read_bytes(), actual + b'\n')
        config.write_bytes(actual)
        ok(self.install_cli('rollback', *options, home=home))
        self.assertEqual(config.read_bytes(), self.original_config)

    def test_install_refuses_a_modified_release_before_changing_settings(self):
        untouched = self.scenario / 'hooks.json'
        copied = self.scenario / 'tampered'
        shutil.copytree(self.source, copied)
        (copied / 'implementation/code-guard/policies.json').write_text('{}\n')
        result = run(sys.executable, copied / 'install.py', 'install', '--home', self.scenario / 'never',
                     '--host', 'codex', '--config', untouched)
        self.assertEqual(result.returncode, 2)
        self.assertIn('checksum mismatch', result.stderr)
        self.assertFalse(untouched.exists())

    def test_upgrade_only_replaces_the_explicit_legacy_hook(self):
        config = self.scenario / 'hooks.json'
        legacy = 'expected legacy command'
        previous = {'hooks': {'PostToolUse': [{'matcher': 'one-tool', 'hooks': [
            {'type': 'command', 'command': legacy}, {'type': 'command', 'command': 'other integration'}]}]}}
        config.write_text(json.dumps(previous))
        args = ('--host', 'codex', '--config', config, '--replace-command', legacy)
        ok(self.install_cli('install', *args))
        updated = json.loads(config.read_text())['hooks']
        self.assertEqual(updated['PostToolUse'][0]['matcher'], 'one-tool')
        self.assertEqual(updated['PostToolUse'][0]['hooks'], [{'type': 'command', 'command': 'other integration'}])
        self.assertEqual(len(updated['Stop']), 1)
        ok(self.install_cli('install', *args))
        self.assertEqual(json.loads(config.read_text())['hooks'], updated)
        ok(self.install_cli('rollback', *args))
        self.assertEqual(json.loads(config.read_text()), previous)

    def test_interrupted_activation_resumes_with_the_previous_registration(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('p1_settings', self.source / 'implementation/toolkit/settings_merge.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = self.scenario / 'hooks.json'
        config.write_bytes(self.original_config)
        def expected(command):
            return {'Stop': {'hooks': [{'type': 'command', 'command': command}]}}
        module.activate(module.plan(self.scenario, config, expected('agent-toolkit previous'), []))
        prior = config.read_bytes()
        update = module.plan(self.scenario, config, expected('agent-toolkit next'), [])
        atomic = module.atomic_write
        def fail_settings_only(path, data, mode=0o600):
            if path == config:
                raise OSError('simulated process stop before settings replacement')
            return atomic(path, data, mode)
        with patch.object(module, 'atomic_write', side_effect=fail_settings_only):
            with self.assertRaisesRegex(OSError, 'simulated process stop'):
                module.activate(update)
        self.assertEqual(config.read_bytes(), prior)
        module.activate(module.plan(self.scenario, config, expected('agent-toolkit next'), []))
        self.assertNotIn(b'agent-toolkit previous', config.read_bytes())
        module.rollback(self.scenario, config)
        self.assertEqual(config.read_bytes(), prior)
        module.activate(module.plan(self.scenario, config, expected('agent-toolkit latest'), []))
        self.assertNotIn(b'agent-toolkit previous', config.read_bytes())

    def exercise_host(self, host, close_event, rejection):
        config = self.scenario / 'host.json'
        installed = self.installed(host, config)
        hooks = json.loads(config.read_text())['hooks']
        guard = installed['guard']
        command = shlex.split(hooks[close_event][0]['hooks'][0]['command'])
        repo = self.repo()
        session = f'fixture-{host}'
        event = {'session_id': session, 'hook_event_name': close_event, 'cwd': str(repo)}
        if host != 'codex':
            start = json.loads(ok(run(*command, input_text=json.dumps(dict(event, hook_event_name='SessionStart')))))
            self.assertIn('--session ' + session, start['hookSpecificOutput']['additionalContext'])
        else:
            self.assertEqual(list(hooks), ['Stop'])
        self.assertEqual(json.loads(ok(run(*command, input_text=json.dumps(event)))), {})
        task = [sys.executable, guard, '--session', session]
        ok(run(*task, 'begin', '--repo', repo))
        (repo / 'app.js').write_text('console.log(2);\n')
        (repo / 'README.md').write_text('The output is 2.\n')
        blocked = json.loads(ok(run(*command, input_text=json.dumps(event))))
        self.assertEqual(blocked['decision'], rejection)
        repeated = json.loads(ok(run(*command, input_text=json.dumps(dict(event, stop_hook_active=True)))))
        self.assertFalse(repeated['continue'])
        self.assertNotIn('decision', repeated)
        check = "import subprocess; assert subprocess.check_output(['node','app.js']) == b'2\\n'"
        ok(run(*task, 'verify', '--repo', repo, '--review', 'Expected output is 2.', '--', sys.executable, '-c', check))
        self.assertEqual(json.loads(ok(run(*command, input_text=json.dumps(event)))), {})

    def test_codex_stop_adapter(self):
        self.exercise_host('codex', 'Stop', 'block')

    def test_native_hook_rejects_integrity_failure_for_registered_changes(self):
        config = self.scenario / 'hooks.json'
        guard = self.installed('codex', config)['guard']
        repo = self.repo()
        task = [sys.executable, guard, '--session', 'integrity-fixture']
        ok(run(*task, 'begin', '--repo', repo))
        (repo / 'app.js').write_text('console.log(3);\n')
        policies = Path(guard).parent.parent / 'code-guard/policies.json'
        before = policies.read_bytes()
        payload = json.dumps({'session_id': 'integrity-fixture', 'hook_event_name': 'Stop', 'cwd': str(repo)})
        policies.write_bytes(before + b'\n')
        try:
            response = json.loads(ok(run(sys.executable, guard, 'hook', 'codex', input_text=payload)))
            self.assertEqual(response.get('decision'), 'block')
            self.assertIn('Guard installation changed', response['reason'])
        finally:
            policies.write_bytes(before)

    def test_claude_stop_adapter(self):
        self.exercise_host('claude', 'Stop', 'block')

    def test_gemini_after_agent_adapter(self):
        self.exercise_host('gemini', 'AfterAgent', 'deny')

    @unittest.skipUnless(shutil.which('codex'), 'native Codex CLI not installed')
    def test_installed_launcher_in_native_codex(self):
        sys.path.insert(0, str(self.source / 'implementation/code-guard'))
        from test_stop_runtime_p0 import StopRuntimeTests, StopServer

        repo = self.repo()
        (repo / 'read-marker.txt').write_text('READ_RESULT_PRESERVED\n')
        self.commit(repo)
        home = self.scenario / 'Native home'
        home.mkdir()
        config = home / 'hooks.json'
        installed = self.installed('codex', config)
        events = trace_native_config(config)
        fixture = StopServer(repo, 'repair', installed['task_command'])
        worker = threading.Thread(target=fixture.serve_forever, daemon=True)
        worker.start()
        try:
            StopRuntimeTests.run_cli(self, home, repo, fixture)
        finally:
            fixture.shutdown()
            fixture.server_close()
            worker.join(timeout=3)
        self.assertIsNone(fixture.failure)
        results = [json.loads(line) for line in events.read_text().splitlines()]
        self.assertEqual(results[0]['decision'], 'block')
        self.assertEqual(results[-1], {})

    def test_ci_uses_committed_bytes_and_preserves_incremental_rules(self):
        guard = self.installed()['guard']
        repo = self.repo()
        (repo / 'legacy.js').write_text('new Buffer(2);\n')
        base = self.commit(repo)
        cli = [sys.executable, guard, 'check', '--repo', repo, '--base', base, '--head']
        (repo / 'README.md').write_text('Only documentation.\n')
        doc = self.commit(repo)
        self.assertEqual(json.loads(ok(run(*cli, doc)))['status'], 'not_applicable')
        (repo / 'app.js').write_text('console.log(2);\n')
        good = self.commit(repo)
        result = json.loads(ok(run(*cli, good)))
        self.assertEqual((result['status'], result['files']), ('passed', ['app.js']))
        (repo / '.gitattributes').write_text('bad.js export-ignore\n')
        (repo / 'bad.js').write_text('new Buffer(3);\n')
        bad = self.commit(repo)
        (repo / 'bad.js').write_text('console.log("clean worktree edit");\n')
        blocked = run(*cli, bad)
        self.assertEqual(blocked.returncode, 1)
        self.assertIn('bad.js', json.loads(blocked.stdout)['diagnostics'][0])
        missing = run(*cli, 'not-a-ref')
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(json.loads(missing.stdout)['status'], 'unavailable')

    def test_workflow_uses_base_artifact_and_rejects_the_candidate(self):
        repo = self.repo()
        vendor = repo / 'vendor'
        vendor.mkdir()
        archive = vendor / 'agent-toolkit-p1.zip'
        shutil.copyfile(self.root / 'archive' / ARCHIVE_NAME, archive)
        base = self.commit(repo)
        archive.write_bytes(b'invalid candidate version of the toolkit')
        (repo / 'app.js').write_text('new Buffer(1);\n')
        head = self.commit(repo)
        remote = self.scenario / 'remote.git'
        self.git(repo, 'clone', '-q', '--bare', str(repo), str(remote))
        self.git(repo, 'push', '-q', str(remote), f'{head}:refs/pull/42/head')
        worktree = self.scenario / 'base checkout'
        self.git(repo, 'clone', '-q', str(remote), str(worktree))
        self.git(worktree, 'checkout', '-q', '--detach', base)
        workflow = (self.source / 'examples/github-actions.yml').read_text()
        script = textwrap.dedent(workflow.split('        run: |\n', 1)[1])
        variables = {'BASE_SHA': base, 'HEAD_SHA': head, 'PR_NUMBER': '42',
                     'GITHUB_WORKSPACE': str(worktree), 'npm_config_offline': 'true'}
        result = run('bash', '-c', script, cwd=worktree, extra_env=variables)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('app.js', json.loads(result.stdout)['diagnostics'][0])
        self.assertEqual(self.git(worktree, 'rev-parse', 'HEAD'), base)


def trace_native_config(config):
    values = json.loads(config.read_text())
    hook = values['hooks']['Stop'][0]['hooks'][0]
    command = shlex.split(hook['command'])
    wrapper = config.parent / 'record_generated_hook.py'
    events = config.parent / 'toolkit-hook-results.jsonl'
    wrapper.write_text(f'''import json, subprocess, sys
from pathlib import Path
result = subprocess.run({command!r}, input=sys.stdin.read(), text=True, capture_output=True, check=True)
with Path({str(events)!r}).open('a') as output:
    output.write(json.dumps(json.loads(result.stdout)) + '\\n')
print(result.stdout)
''')
    hook['command'] = shlex.join([sys.executable, str(wrapper)])
    config.write_text(json.dumps(values))
    return events


if __name__ == '__main__':
    unittest.main()
