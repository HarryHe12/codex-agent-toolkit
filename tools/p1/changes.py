"""Compare Git blobs with the installed source guard, without a candidate checkout."""
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile

import hook


SKIP = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'state'}


def revision(repo, ref):
    return git(repo, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}').decode().strip()


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, timeout=30)
    return result.stdout


def change_names(repo, base, head):
    raw = git(repo, 'diff', '--no-ext-diff', '--no-textconv', '--name-only', '-z', '--no-renames',
              base, head, '--')
    paths = [PurePosixPath(os.fsdecode(name)) for name in raw.split(b'\0') if name]
    return [path for path in paths if not SKIP.intersection(path.parts)]


def tree_sources(repo, commit, paths):
    wanted = set(paths)
    entries = {}
    raw = git(repo, 'ls-tree', '-r', '-z', '--full-tree', commit)
    for row in raw.split(b'\0'):
        if not row:
            continue
        metadata, _, name = row.partition(b'\t')
        path = PurePosixPath(os.fsdecode(name))
        mode, kind, oid = metadata.decode().split()
        if path not in wanted or kind != 'blob':
            continue
        if mode == '120000' or path.suffix.lstrip('.').lower() in hook.SOURCE_EXTENSIONS:
            entries[path] = mode, oid
    return entries


def snapshot(repo, entries, root):
    root.mkdir()
    for path, (mode, oid) in entries.items():
        if path.is_absolute() or '..' in path.parts:
            raise ValueError(f'Unsafe Git tree path: {path}')
        target = root.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == '120000':
            target.symlink_to('unavailable-link-' + oid)
        else:
            target.write_bytes(git(repo, 'cat-file', 'blob', oid))


def assess(repo, base_ref, head_ref):
    base = revision(repo, base_ref)
    head = revision(repo, head_ref)
    paths = change_names(repo, base, head)
    before = tree_sources(repo, base, paths)
    after = tree_sources(repo, head, paths)
    outcome = {'base': base, 'head': head, 'files': sorted(map(str, after)), 'diagnostics': []}
    if not after:
        return dict(outcome, status='not_applicable')
    with tempfile.TemporaryDirectory(prefix='toolkit-ci-') as directory:
        root = Path(directory)
        snapshot(repo, before, root / 'base')
        snapshot(repo, after, root / 'head')
        prior = hook.run_scan(root / 'base', {}, policy_root=repo, source_only=True)
        current = hook.run_scan(root / 'head', {}, policy_root=repo, source_only=True)
        errors = hook.failures(current, prior, {str(path) for path in after})
    return dict(outcome, status='failed' if errors else 'passed', diagnostics=errors)
