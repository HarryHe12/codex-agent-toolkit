"""Preflight, install, verify and roll back an Agent Toolkit release."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

import hosts
import package_files
import settings_merge


GUARD = 'implementation/toolkit/guard.py'
ENGINE = 'implementation/code-guard'


def executable(value, option, companion=None):
    found = shutil.which(value, path=str(companion)) if companion and not os.path.isabs(value) else None
    found = found or shutil.which(value)
    if not found:
        raise ValueError(f'Executable missing: {value}. Install it or supply {option}.')
    return Path(found).absolute()


def dependencies(args):
    node = executable(args.node, '--node')
    npm = executable(args.npm, '--npm', node.parent)
    env = dict(os.environ, PATH=str(node.parent) + os.pathsep + os.environ.get('PATH', ''))
    if args.offline:
        env['npm_config_offline'] = 'true'
    version = subprocess.check_output([str(node), '--version'], env=env, text=True, timeout=10).strip()
    major = int(version.lstrip('v').split('.')[0])
    if major != 24:
        raise ValueError(f'This release expects Node 24. Selected {node} reports {version}.')
    return node, npm, env


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('doctor', 'plan', 'install', 'verify', 'rollback'))
    parser.add_argument('--home', type=Path, default=Path.home() / '.agent-toolkit')
    parser.add_argument('--host', choices=('manual', *hosts.EVENTS), default='manual')
    parser.add_argument('--config', type=Path, help='Host settings JSON path override.')
    parser.add_argument('--node', default=os.environ.get('CODEX_GUARD_NODE') or 'node')
    parser.add_argument('--npm', default='npm')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--replace-command', action='append', default=[], metavar='COMMAND',
                        help='Replace this exact, explicitly selected old hook command.')
    return parser.parse_args()


def target_settings(args):
    if args.host == 'manual':
        if args.config or args.replace_command:
            raise ValueError('Choose a native --host when supplying host settings options.')
        return None
    codex_home = os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')
    target = args.config or hosts.settings_path(args.host, codex_home, Path.home())
    if target.is_symlink():
        raise ValueError('The settings path is a symlink. Use --config with its resolved target.')
    return target.expanduser().resolve()


@contextmanager
def exclusive(home):
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (home / '.install.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('An install or rollback is in progress for this home. Retry afterwards.') from error
        yield


def npm_ci(directory, npm, env):
    subprocess.run([str(npm), 'ci', '--omit=optional', '--ignore-scripts', '--no-audit', '--no-fund'],
                   cwd=directory / ENGINE, env=env, check=True, timeout=180, stdout=sys.stderr)


def smoke(directory, node, env):
    return subprocess.run([sys.executable, str(directory / GUARD), '--node', str(node), 'smoke'],
                          env=env, check=True, timeout=90, capture_output=True, text=True).stdout.strip()


def copy_release(source, stage, manifest, manifest_bytes):
    for name in manifest['sha256']:
        destination = stage / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package_files.member(source, name), destination)
    (stage / package_files.MANIFEST).write_bytes(manifest_bytes)
    package_files.verify_files(stage, manifest)


def ensure_integrity(directory, manifest):
    path = directory / ENGINE / 'integrity.json'
    checked = {str(directory / name): checksum for name, checksum in manifest['sha256'].items()
               if name.startswith('implementation/')}
    expected = settings_merge.dump(checked)
    if path.exists():
        if path.read_bytes() != expected:
            raise ValueError(f'Installed integrity manifest differs from the release: {path}')
    else:
        settings_merge.atomic_write(path, expected)


def install_version(source, directory, manifest, raw, node, npm, env):
    if directory.exists():
        package_files.verify_files(directory, manifest)
        if (directory / package_files.MANIFEST).read_bytes() != raw:
            raise ValueError('The existing version has a different manifest.')
        ensure_integrity(directory, manifest)
        try:
            return smoke(directory, node, env)
        except subprocess.CalledProcessError as error:
            if 'Missing code guard package' not in error.stderr:
                raise
            npm_ci(directory, npm, env)
            return smoke(directory, node, env)
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.install-', dir=directory.parent) as folder:
        stage = Path(folder).resolve() / 'release'
        copy_release(source, stage, manifest, raw)
        npm_ci(stage, npm, env)
        smoke(stage, node, env)
        os.replace(stage, directory)
    ensure_integrity(directory, manifest)
    return smoke(directory, node, env)


def update_plan(args, directory, node, config):
    if config is None:
        return None
    wanted = hosts.registrations(args.host, directory / GUARD, Path(sys.executable), node)
    return settings_merge.plan(args.home, config, wanted, args.replace_command)


def details(args, directory, node, npm, update):
    codex_home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    reviewer = bool(shutil.which('codex') and (codex_home / 'auth.json').is_file())
    result = {'home': str(args.home), 'host': args.host, 'guard': str(directory / GUARD),
              'skills': str(directory / 'implementation/skills'), 'node': str(node), 'npm': str(npm),
              'task_command': shlex.join([sys.executable, str(directory / GUARD), '--node', str(node)]),
              'codex_reviewer_login_detected': reviewer, 'installed': directory.is_dir()}
    if update:
        command = {'codex': 'codex', 'claude': 'claude', 'gemini': 'gemini'}[args.host]
        result.update(config=str(update['path']), events=update['events'],
                      would_change_settings=update['before'] != update['after'],
                      host_cli_detected=bool(shutil.which(command)),
                      activation='Confirm native hook loading and run the host acceptance workflow.')
    return result


def execute(args):
    if os.name != 'posix' or sys.version_info < (3, 9):
        raise ValueError('The installer requires a Unix-like host and Python 3.9 or later.')
    args.home = args.home.expanduser().resolve()
    config = target_settings(args)
    if args.action == 'rollback':
        if config is None:
            raise ValueError('Manual installations have no host settings to roll back.')
        with exclusive(args.home):
            settings_merge.rollback(args.home, config)
        return {'status': 'rolled_back', 'config': str(config)}
    source = Path(__file__).resolve().parents[2]
    manifest, raw, version_key = package_files.release(source)
    package_files.verify_files(source, manifest)
    directory = args.home / 'versions' / version_key
    node, npm, env = dependencies(args)
    update = update_plan(args, directory, node, config)
    report = details(args, directory, node, npm, update)
    if args.action == 'install':
        with exclusive(args.home):
            report['smoke'] = install_version(source, directory, manifest, raw, node, npm, env)
            if update:
                settings_merge.activate(update)
        report.update(status='installed', installed=True)
    elif args.action == 'verify' or (args.action == 'doctor' and directory.is_dir()):
        package_files.verify_files(directory, manifest)
        if not (directory / ENGINE / 'integrity.json').is_file():
            raise ValueError('Installation integrity is incomplete. Re-run install before activation.')
        report['smoke'] = smoke(directory, node, env)
        if update and update['before'] != update['after']:
            raise ValueError('Installed runtime passed, but selected host hooks differ from the install plan.')
        report['status'] = 'verified_runtime'
    else:
        report['status'] = 'ready'
    return report


def main():
    try:
        print(json.dumps(execute(arguments()), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else ''
        print(f'Agent Toolkit: {detail or error}', file=sys.stderr)
        raise SystemExit(2) from error
