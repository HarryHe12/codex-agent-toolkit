"""Test opt-in acceptance against the shared CLI and native Stop protocol."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from build_p1_release import ARCHIVE_NAME, export
from build_p2_release import RELEASE, build, collect


APP = '''from pathlib import Path

stock = int(Path('stock.csv').read_text())
seen = set()
for token in ('R-1', 'R-1'):
    if token not in seen:
        stock -= 2
    seen.add(token)
print(f'Available: {stock}')
'''
DOCS = '''# Reservations

Replaying the same reservation ID deducts stock once.

```text
Available: 8
```
'''
CHECK = '''import subprocess
import sys

observed = subprocess.check_output([sys.executable, 'inventory.py'], text=True).strip()
if observed != 'Available: 8':
    print('DUPLICATE_RESERVATION_DEBIT', file=sys.stderr)
    sys.exit(1)
print('RETRY_CHECK_PASSED')
'''
WEAK = '''import subprocess
import sys

observed = subprocess.check_output([sys.executable, 'inventory.py'], text=True).strip()
assert observed.startswith('Available: ')
print('RETRY_CHECK_PASSED')
'''


def run(*args, cwd=None, hook_input=None):
    env = dict(os.environ)
    env.pop('CODEX_GUARD_NODE', None)
    return subprocess.run(list(map(str, args)), cwd=cwd, env=env, input=hook_input,
                          text=True, capture_output=True, timeout=200)


def ok(result):
    if result.returncode:
        raise AssertionError(result.stdout + '\n' + result.stderr)
    return result.stdout


def fixture_recipe(exported):
    recipe = json.loads((exported / 'examples/acceptance.json').read_text())
    case = recipe['scenarios'][0]
    case['inputs'] = ['inventory.py', 'check_retry.py', 'stock.csv']
    case['document'] = 'README.md'
    case['example']['argv'] = [sys.executable, 'inventory.py']
    case['test']['argv'] = [sys.executable, 'check_retry.py']
    case['counterexample']['before'] = 'if token not in seen:'
    return recipe


class AcceptanceReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = TemporaryDirectory(prefix='toolkit P2 tests ')
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name).resolve()
        entries = collect()
        archive = build(entries, cls.root / 'release')
        if (RELEASE / ARCHIVE_NAME).is_file():
            assert (RELEASE / ARCHIVE_NAME).read_bytes() == archive.read_bytes()
        public = cls.root / 'public'
        export(entries, public)
        cls.source = cls.root / 'source'
        with ZipFile(archive) as package:
            package.extractall(cls.source)
        assert all((public / name).read_bytes() == (cls.source / name).read_bytes() for name in entries)
        report = ok(run(sys.executable, cls.source / 'install.py', 'install', '--offline',
                        '--home', cls.root / 'installed'))
        cls.guard = Path(json.loads(report)['guard'])

    def setUp(self):
        folder = TemporaryDirectory(dir=self.root)
        self.addCleanup(folder.cleanup)
        self.repo = Path(folder.name)
        self.session = self.repo.name
        self.recipe = fixture_recipe(self.source)
        self.write_plan()
        (self.repo / 'inventory.py').write_text(APP)
        (self.repo / 'stock.csv').write_text('10\n')
        (self.repo / 'check_retry.py').write_text(CHECK)
        (self.repo / 'README.md').write_text(DOCS)
        ok(run('git', 'init', '-qb', 'main', cwd=self.repo))
        ok(run('git', 'add', '.', cwd=self.repo))
        ok(run('git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
               'commit', '-qm', 'Acceptance fixture', cwd=self.repo))
        ok(self.task('begin', '--repo', self.repo))

    def write_plan(self):
        folder = self.repo / '.agent-toolkit'
        folder.mkdir(exist_ok=True)
        (folder / 'acceptance.json').write_text(json.dumps(self.recipe))

    def task(self, *args):
        return run(sys.executable, self.guard, '--session', self.session, *args)

    def verify(self, docs_only=False, acceptance=True):
        args = ('verify', '--repo', self.repo, '--review', 'Checked reservation and documented output')
        if acceptance:
            args += ('--acceptance',)
        if docs_only:
            return self.task(*args, '--docs-only')
        return self.task(*args, '--', sys.executable, 'check_retry.py')

    def stop(self):
        payload = json.dumps({'session_id': self.session, 'cwd': str(self.repo), 'hook_event_name': 'Stop'})
        return json.loads(ok(run(sys.executable, self.guard, 'hook', 'codex', hook_input=payload)))

    def evidence(self, result):
        return next(json.loads(row) for row in ok(result).splitlines() if row.startswith('{"acceptance"'))

    def test_same_command_rejects_defect_without_changing_the_worktree(self):
        fixture = self.repo / 'inventory.py'
        fixture.write_text(APP + '\n')
        (self.repo / 'README.md').write_text(DOCS + '\nUpdated reservation entry.\n')
        proof = self.evidence(self.verify())
        self.assertEqual(proof['results'][0]['example'], 'passed')
        self.assertEqual(proof['results'][0]['counterexample'], 'rejected')
        self.assertIn('stock.csv', proof['state']['inputs'])
        self.assertEqual(fixture.read_text(), APP + '\n')
        self.assertEqual(self.stop(), {})

    def test_drift_in_documented_example_stays_blocked(self):
        (self.repo / 'README.md').write_text(DOCS.replace('Available: 8', 'Available: 6'))
        result = self.verify(docs_only=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('missing the expected output', result.stderr)
        self.assertEqual(self.stop()['decision'], 'block')

    def test_verified_business_example_must_match_runtime_even_if_test_passes(self):
        (self.repo / 'inventory.py').write_text(APP.replace('stock -= 2', 'stock -= 4'))
        (self.repo / 'check_retry.py').write_text(WEAK)
        (self.repo / 'README.md').write_text(DOCS + '\nReviewed.\n')
        result = self.verify()
        self.assertIn('did not produce the documented output', result.stderr)
        self.assertEqual(self.stop()['decision'], 'block')

    def test_weak_test_does_not_get_an_acceptance_receipt(self):
        (self.repo / 'check_retry.py').write_text(WEAK)
        (self.repo / 'README.md').write_text(DOCS + '\nUpdated regression.\n')
        result = self.verify()
        self.assertEqual(result.returncode, 2)
        self.assertIn('same test accepted the representative defect', result.stderr)
        self.assertEqual(self.stop()['decision'], 'block')

    def test_a_crashing_counterexample_is_not_a_correct_rejection(self):
        config = self.recipe['scenarios'][0]['counterexample']
        config['after'] = 'if (:'
        self.write_plan()
        (self.repo / 'README.md').write_text(DOCS + '\nProposed alternative fixture.\n')
        result = self.verify()
        self.assertEqual(result.returncode, 2)
        self.assertIn('failed for an unverified reason', result.stderr)

    def test_data_input_change_invalidates_evidence(self):
        (self.repo / 'README.md').write_text(DOCS + '\nReviewed example.\n')
        self.evidence(self.verify(docs_only=True))
        (self.repo / 'stock.csv').write_text('11\n')
        self.assertEqual(self.stop()['decision'], 'block')
        result = self.verify()
        self.assertEqual(result.returncode, 2)

    def test_unrelated_document_change_does_not_run_the_scenario(self):
        (self.repo / 'changelog.md').write_text('Improve wording.\n')
        proof = self.evidence(self.verify(docs_only=True, acceptance=False))
        self.assertEqual(proof['acceptance'], 'not_applicable')
        self.assertEqual(proof['results'], [])
        self.assertEqual(self.stop(), {})

    def test_a_project_plan_does_not_authorize_new_commands_by_itself(self):
        (self.repo / 'README.md').write_text(DOCS + '\nReviewed scope.\n')
        result = self.verify(docs_only=True, acceptance=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn('rerun verify with --acceptance', result.stderr)
        self.assertEqual(self.stop()['decision'], 'block')

    def test_timeout_is_an_incomplete_verification(self):
        config = self.recipe['scenarios'][0]
        config['timeout_seconds'] = 1
        config['example']['argv'] = [sys.executable, '-c', 'import time; time.sleep(8)']
        self.write_plan()
        (self.repo / 'README.md').write_text(DOCS + '\nReviewed timeout fixture.\n')
        result = self.verify()
        self.assertEqual(result.returncode, 2)
        self.assertIn('timed out after 1s', result.stderr)
        self.assertEqual(self.stop()['decision'], 'block')


if __name__ == '__main__':
    unittest.main()
