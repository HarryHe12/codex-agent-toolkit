"""Validate the shipped archive, export tree and same-task dependency recovery."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile

from build_p0_release import ARCHIVE_NAME, RELEASE, SOURCE, build, collect, export, validate


def run(args, cwd, env, fixture_input=None):
    return subprocess.run(args, cwd=cwd, env=env, input=fixture_input, text=True,
                          capture_output=True, timeout=240)


def checked(result):
    if result.returncode:
        raise AssertionError(result.stdout + '\n' + result.stderr)
    return result.stdout


def installed_sources(entries, root):
    build(entries, root / 'release')
    output = root / 'release' / ARCHIVE_NAME
    public = root / 'public'
    export(entries, public)
    install = root / 'unpacked'
    with zipfile.ZipFile(output) as archive:
        archive.extractall(install)
    if any((install / name).read_bytes() != (public / name).read_bytes() for name in entries):
        raise AssertionError('Public export differs from the archive contents.')
    if (RELEASE / ARCHIVE_NAME).is_file() and (RELEASE / ARCHIVE_NAME).read_bytes() != output.read_bytes():
        raise AssertionError('Checked-in release differs from a clean rebuild.')
    return install / 'implementation'


def exercise_recovery(home, root, env):
    project = root / 'project'
    project.mkdir()
    engine = home / 'code-guard'
    cmd = [sys.executable, str(engine / 'closeout.py')]
    env = dict(env, CODEX_SESSION_ID='release-recovery-fixture')
    (project / 'app.js').write_text('console.log(1);\n')
    for args in (('init', '-q'), ('add', 'app.js'), ('commit', '-qm', 'Fixture baseline')):
        checked(run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                     *args], project, env))
    checked(run([*cmd, 'begin'], project, env))
    (project / 'app.js').write_text('console.log(2);\n')
    (project / 'README.md').write_text('The app outputs 2.\n')
    shutil.rmtree(engine / 'node_modules/eslint')
    event = json.dumps({'cwd': str(project), 'session_id': env['CODEX_SESSION_ID'], 'hook_event_name': 'Stop'})
    blocked = json.loads(checked(run([*cmd, 'stop'], project, env, event)))
    if blocked.get('decision') != 'block' or 'Missing code guard package(s): eslint' not in blocked['reason']:
        raise AssertionError(blocked)
    diagnostic = next(line for line in blocked['reason'].splitlines() if line.startswith('cd '))
    if shlex.split(diagnostic)[:3] != ['cd', str(engine), '&&']:
        raise AssertionError('Expected recovery to install only the disposable guard dependencies.')
    checked(run(['/bin/sh', '-c', diagnostic], project, env))
    assertion = "import subprocess; assert subprocess.check_output(['node','app.js']) == b'2\\n'"
    checked(run([*cmd, 'verify', '--review', 'Application output is 2.', '--',
                 sys.executable, '-c', assertion], project, env))
    if json.loads(checked(run([*cmd, 'stop'], project, env, event))) != {}:
        raise AssertionError('The original session did not pass after reinstalling the dependency.')
    print('Real npm reinstall and original-session recovery passed.')


def suite(directory, env):
    outcome = run([sys.executable, '-m', 'unittest', 'discover', '-v'], directory, env)
    checked(outcome)
    summary = [line for line in outcome.stderr.splitlines() if line.startswith(('Ran ', 'OK'))]
    print(directory.name, ' '.join(summary))


def validate_release(entries, root, offline):
    home = installed_sources(entries, root)
    env = dict(os.environ, CODEX_HOME=str(home), CODEX_GUARD_MD_HOME=str(home / 'md-governance'))
    if offline:
        env['npm_config_offline'] = 'true'
    code = home / 'code-guard'
    checked(run(['npm', 'ci', '--omit=optional', '--ignore-scripts', '--no-audit', '--no-fund'], code, env))
    exercise_recovery(home, root, env)
    md_home = root / 'md-validation'
    shutil.copytree(home / 'md-governance', md_home / 'md-governance')
    with ThreadPoolExecutor(max_workers=2) as pool:
        checks = [pool.submit(suite, code, env),
                  pool.submit(suite, md_home / 'md-governance', dict(env, CODEX_HOME=str(md_home)))]
        for check in checks:
            check.result()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--draft-skill', type=Path, help='Test an unpublished draft in the disposable sandbox.')
    args = parser.parse_args()
    entries = collect(args.draft_skill or (SOURCE / 'portable-skill.md'))
    validate(entries)
    with tempfile.TemporaryDirectory(prefix='agent-toolkit-release-test-') as folder:
        validate_release(entries, Path(folder).resolve(), args.offline)


if __name__ == '__main__':
    main()
