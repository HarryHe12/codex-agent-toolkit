"""Read and verify versioned release files before installing executable sources."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


MANIFEST = 'PACKAGE-MANIFEST.json'


def release(root):
    path = root / MANIFEST
    if path.is_symlink():
        raise ValueError('The package manifest must be a regular file.')
    raw = path.read_bytes()
    manifest = json.loads(raw)
    version = manifest['version']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', version):
        raise ValueError('Invalid package version.')
    required = {'install.py', 'implementation/toolkit/guard.py',
                'implementation/toolkit/installer.py', 'implementation/code-guard/package-lock.json'}
    if not required.issubset(manifest['sha256']):
        raise ValueError('The manifest does not cover the installer and runtime.')
    return manifest, raw, version + '-' + hashlib.sha256(raw).hexdigest()[:12]


def member(root, name):
    relative = PurePosixPath(name)
    path = root / relative
    if not relative.parts or relative.is_absolute() or '..' in relative.parts or name == MANIFEST:
        raise ValueError(f'Invalid manifest path: {name}')
    if not path.is_file() or path.is_symlink() or path.resolve() != path:
        raise ValueError(f'Missing or unsafe package file: {path}')
    return path


def verify_files(root, manifest):
    for name, expected in manifest['sha256'].items():
        path = member(root, name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f'Package file checksum mismatch: {path}')
