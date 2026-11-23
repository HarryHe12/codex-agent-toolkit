"""Report actionable dependency failures before running the code scanner."""

import json
import os
import re
from pathlib import Path
import shlex
import shutil


class ScannerDependencyError(RuntimeError):
    """A missing scanner dependency that can be installed and retried."""


RESUME = (
    'If you are authorized to install dependencies, install them and rerun the '
    'same verification. Otherwise show the user the missing dependencies and '
    'install instructions below, then ask them to reply "继续" in this chat '
    'after installation. Retry the failed step and resume the current task. '
    'Verification is incomplete until the retry passes.'
)


def check_dependencies(base, node):
    if not node or not shutil.which(node):
        raise ScannerDependencyError(
            f'Node.js is missing or not executable ({node or "node"}). Install '
            'Node.js 24 LTS with npm, then make node available to the hook '
            'or set CODEX_GUARD_NODE to its absolute executable path.\n' + RESUME
        )
    manifest = base / 'package.json'
    if not manifest.is_file():
        return
    packages = json.loads(manifest.read_text())['dependencies']
    missing = [
        name for name in packages
        if not (base / 'node_modules' / name / 'package.json').is_file()
    ]
    if missing:
        raise package_error(base, node, ', '.join(missing))


def package_error(base, node, missing):
    node_directory = Path(shutil.which(node)).parent
    npm = shutil.which('npm', path=str(node_directory))
    npm = npm or shutil.which('npm')
    if not npm:
        setup = 'Install npm with Node.js 24 LTS and make both available on PATH, then run'
        command = 'npm'
    else:
        setup = 'Run'
        command = f'PATH={shlex.quote(os.fspath(node_directory))}:"$PATH" {shlex.quote(npm)}'
    return ScannerDependencyError(
        f'Missing code guard package(s): {missing}. {setup}:\n'
        f'cd {shlex.quote(os.fspath(base))} && {command} ci '
        '--omit=optional --ignore-scripts --no-audit --no-fund\n' + RESUME
    )


def raise_scan_error(base, node, stderr):
    if 'ERR_MODULE_NOT_FOUND' in stderr or 'MODULE_NOT_FOUND' in stderr:
        missing = re.search(r"Cannot find (?:package|module) ['\"]([^'\"]+)", stderr)
        if missing:
            raise package_error(base, node, missing.group(1))
    raise RuntimeError(stderr[-3000:])
