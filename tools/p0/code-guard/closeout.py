"""Register edits explicitly and verify them at Codex Stop."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import hook
from turn_evidence import turn_writes

PROSE = {'.md', '.mdx', '.rst', '.adoc', '.txt'}
CONFIG = {'json', 'jsonc', 'toml', 'yaml', 'yml', 'ini', 'cfg', 'conf', 'xml',
          'plist', 'properties', 'lock', 'env', 'gradle'}
CONFIG_NAMES = {'makefile', 'dockerfile', 'containerfile', 'gemfile', 'procfile',
                'podfile', 'vagrantfile', 'rakefile', 'justfile', 'jenkinsfile'}
EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'


def digest(contents):
    return hashlib.sha256(contents).hexdigest()


def git(root, *args):
    return subprocess.run(
        ['git', '-C', str(root), *args], capture_output=True, check=True, timeout=10
    ).stdout


def head(root):
    result = subprocess.run(
        ['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD'],
        capture_output=True, timeout=10,
    )
    return result.stdout.decode().strip() if result.returncode == 0 else EMPTY_TREE


def names(raw):
    return {os.fsdecode(name) for name in raw.split(b'\0') if name}


def blob_hash(contents, mode):
    return digest(mode + b" " + contents)


def work_hash(path):
    if path.is_symlink():
        return blob_hash(os.fsencode(os.readlink(path)), b'120000')
    if not path.is_file():
        return None
    mode = b'100755' if path.stat().st_mode & 0o100 else b'100644'
    return blob_hash(path.read_bytes(), mode)


def git_hash(root, mode, object_id):
    if mode == b'160000':
        return blob_hash(object_id, mode)
    return blob_hash(git(root, 'cat-file', 'blob', object_id.decode()), mode)


def tree_hash(root, revision, name):
    if revision == EMPTY_TREE:
        return None
    entry = git(root, '--literal-pathspecs', 'ls-tree', '-z', revision, '--', name)
    if not entry:
        return None
    mode, _, object_id = entry.partition(b'\t')[0].split()
    return git_hash(root, mode, object_id)


def indexed_hash(root, name):
    entry = git(root, '--literal-pathspecs', 'ls-files', '-s', '-z', '--', name)
    if not entry:
        return None
    mode, object_id, stage = entry.partition(b'\t')[0].split()
    if stage != b'0':
        raise ValueError(f'Resolve the unmerged index entry for {name}.')
    return git_hash(root, mode, object_id)


def filesystem_states(root):
    result = {}
    for directory, children, files in os.walk(root):
        children[:] = [name for name in children
                       if name not in {'.git', 'node_modules', '.venv', 'venv',
                                       '__pycache__', 'state'}]
        for name in files:
            path = Path(directory) / name
            content = work_hash(path)
            result[str(path.relative_to(root))] = [content, content]
    return result


def states(root, revision):
    if revision is None:
        return filesystem_states(root)
    head_changes = names(git(root, 'diff', '--name-only', '-z', '--no-renames', revision, '--'))
    staged = names(git(root, 'diff', '--cached', '--name-only', '-z', '--no-renames', revision, '--'))
    untracked = names(git(root, 'ls-files', '--others', '--exclude-standard', '-z'))
    current = {}
    for name in sorted(head_changes | staged | untracked):
        working = work_hash(root / name)
        index = indexed_hash(root, name) if name in staged else working
        current[name] = [working, index]
    return current


def changed(root, baseline):
    revision = baseline['head']
    current = states(root, revision)
    before = baseline['files']
    differences = {}
    for name in sorted(current.keys() | before.keys()):
        fallback = before.get(name)
        if name not in before or name not in current:
            clean = tree_hash(root, revision, name) if revision is not None else None
            fallback = [clean, clean]
        previous = before.get(name, fallback)
        present = current.get(name, fallback)
        if present != previous and relevant(name):
            differences[name] = present
    return differences


def session_prefix(session):
    if not session:
        raise ValueError('A Codex session ID is required for edit registration.')
    return 'closeout-' + digest(session.encode())


def registration(session, root):
    prefix = session_prefix(session)
    return hook.STATE / f'{prefix}-{digest(os.fsencode(root))}.json'


def git_root(directory):
    while not directory.exists() and directory != directory.parent:
        directory = directory.parent
    try:
        return Path(os.fsdecode(git(directory, 'rev-parse', '--show-toplevel').strip())).resolve()
    except subprocess.CalledProcessError:
        return None


def target_root(directory, standalone=False):
    directory = directory.resolve(strict=True)
    shared = {Path('/').resolve(), Path.home().resolve(), Path('/tmp').resolve(),
              Path('/var/tmp').resolve(), Path(tempfile.gettempdir()).resolve()}
    if directory in shared or not directory.is_dir():
        raise ValueError(f'Choose a specific Git worktree or standalone project, not {directory}.')
    root = git_root(directory)
    if root is None and not standalone:
        raise ValueError('No Git worktree. Use begin --standalone from a specific non-Git project.')
    if root in shared:
        raise ValueError(f'The Git worktree resolves to a shared directory: {root}.')
    return root or directory


def scan(root, previous, phase):
    print(f'Code guard {phase}: {root}', file=sys.stderr, flush=True)
    try:
        return hook.run_scan(root, previous)
    except subprocess.TimeoutExpired as error:
        raise ValueError(f'Code guard {phase} timed out after {error.timeout}s in {root}. '
                         'Keep the baseline and rerun the same step from the registered worktree.') from error


def begin(root, record):
    if record.exists():
        return
    revision = head(root) if git_root(root) else None
    baseline = {'root': str(root), 'head': revision, 'files': states(root, revision)}
    baseline['scan'] = scan(root, {}, 'pre-edit scan')
    hook.STATE.mkdir(mode=0o700, exist_ok=True)
    hook.save_json(record, baseline)


def prose(name):
    return Path(name).suffix.lower() in PROSE


def relevant(name):
    path = Path(name)
    extension = path.suffix.lstrip('.').lower()
    basename = path.name.lower()
    return (prose(name) or extension in hook.SOURCE_EXTENSIONS or extension in CONFIG
            or basename.split('.')[0] in CONFIG_NAMES or basename.startswith('.env'))


def check_scope(root, differences, command, docs_only):
    if any(working != indexed for working, indexed in differences.values()):
        raise ValueError('Align staged and working file contents for the changed files, then verify.')
    code = any(not prose(name) for name in differences)
    updated_docs = [root / name for name, row in differences.items() if prose(name) and row[0]]
    documented = any(path.is_file() and not path.is_symlink() and path.stat().st_size
                     for path in updated_docs)
    if code and not documented:
        raise ValueError('Update the relevant project documentation or add a change and validation record.')
    if docs_only and code:
        raise ValueError('Run an applicable project verification command for source or configuration changes.')
    if not docs_only and not command:
        raise ValueError('Supply the project verification command after --.')


def code_diagnostics(root, baseline, differences):
    current = scan(root, baseline['scan'], 'change scan')
    owned = set(differences)
    owned.update('@md:' + str(root / name) for name in differences)
    for name, row in current.items():
        previous = baseline['scan'].get(name, {})
        if name.startswith('@md:') and row['hash'] == previous.get('hash'):
            if row['metrics'] != previous.get('metrics'):
                owned.add(name)
    return hook.failures(current, baseline['scan'], owned)


def verify(root, record, review, command, docs_only):
    baseline = json.loads(record.read_text())
    baseline.pop('receipt', None)
    hook.save_json(record, baseline)
    differences = changed(root, baseline)
    if not differences or not review.strip():
        raise ValueError('Verification requires changed files and a substantive review note.')
    check_scope(root, differences, command, docs_only)
    errors = code_diagnostics(root, baseline, differences)
    if errors:
        raise ValueError('\n'.join(errors))
    if command:
        print(f'Running project verification in {root}', file=sys.stderr, flush=True)
        subprocess.run(command, cwd=root, check=True)
    if changed(root, baseline) != differences:
        raise ValueError('The verification command changed the reviewed files. Review and verify again.')
    baseline['receipt'] = {'files': differences, 'review': review, 'command': command,
                           'docs_only': docs_only}
    hook.save_json(record, baseline)


def document_config():
    path = hook.BASE / 'document-project.json'
    return json.loads(path.read_text()) if path.is_file() else None


def project_evidence(root, session, differences):
    config = document_config()
    if config is None:
        return
    import document_hook

    if not document_hook.project_root(root, config):
        return
    path = document_hook.explicit_record(root, session, hook.STATE)
    evidence = json.loads(path.read_text()) if path.exists() else {}
    result = evidence.get('result', {})
    if not result:
        raise ValueError('Run the project document lookup, begin and finish commands.')
    updated = result.get('updated', {})
    for name, entry in updated.items():
        if name not in differences or document_hook.document_digest(root, name) != entry['sha256']:
            raise ValueError(f'Record document evidence again for {name}.')


def check_record(record, session):
    baseline = json.loads(record.read_text())
    root = Path(baseline['root'])
    differences = changed(root, baseline)
    if not differences:
        return
    if pure_fast_forward(root, baseline):
        summary = [f'{root}: clean fast-forward sync. Incoming code is not certified by this task.']
        try:
            summary.extend(code_diagnostics(root, baseline, differences)[:25])
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
            summary.append(f'Incoming change diagnostics unavailable: {error}')
        return '\n'.join(summary)
    if any(not prose(name) for name in differences):
        hook.check_dependencies(hook.BASE, hook.NODE)
    receipt = baseline.get('receipt', {})
    if receipt.get('files') != differences:
        raise ValueError(f'{root}: review changed code and documentation, then run closeout.py verify.')
    check_scope(root, differences, receipt['command'], receipt['docs_only'])
    project_evidence(root, session, differences)


def pure_fast_forward(root, baseline):
    before = baseline['head']
    after = head(root) if before else None
    if not before or before == after or baseline['files']:
        return False
    try:
        previous = git(root, 'rev-parse', 'HEAD@{1}').decode().strip()
        upstream = git(root, 'rev-parse', '@{upstream}').decode().strip()
        action = git(root, 'reflog', '-1', '--format=%gs', 'HEAD').decode().strip()
        if previous != before or upstream != after:
            return False
        if not action.startswith('pull') or not action.endswith(': Fast-forward'):
            return False
        git(root, 'merge-base', '--is-ancestor', before, after)
        return not git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    except subprocess.CalledProcessError:
        return False


def unregistered_writes(event, recorded):
    directories = turn_writes(event)
    if directories is None:
        if event.get('transcript_path'):
            return 'Change verification incomplete: Codex turn evidence could not be read.'
        return None
    roots = {git_root(directory) for directory in directories}
    missing = sorted(str(root) for root in roots if root is not None and root not in recorded)
    if missing:
        return ('Change verification incomplete: write activity without a pre-edit baseline in '
                + ', '.join(missing) + '. Preserve the edits and report them as unverified.')
    return None


def stop(event):
    session = event.get('session_id')
    if not session:
        return {}
    records = list(hook.STATE.glob(session_prefix(session) + '-*.json'))
    diagnostics = []
    try:
        recorded = {Path(json.loads(record.read_text())['root']) for record in records}
        missing = unregistered_writes(event, recorded)
        if missing:
            diagnostics.append(missing)
        if records:
            hook.verify_integrity()
        for record in records:
            message = check_record(record, session)
            if message:
                diagnostics.append(message)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        reason = f'Local quality verification incomplete. {error}'
        if event.get('stop_hook_active'):
            return {'continue': False, 'stopReason': reason, 'systemMessage': reason}
        return {'decision': 'block', 'reason': reason}
    for record in records:
        baseline = json.loads(record.read_text())
        if document_config() is not None:
            import document_hook
            document_hook.explicit_record(Path(baseline['root']), session, hook.STATE).unlink(missing_ok=True)
        record.unlink(missing_ok=True)
    return {'systemMessage': '\n'.join(diagnostics)} if diagnostics else {}


def audit(root):
    stored = hook.STATE / ('baseline-' + digest(os.fsencode(root)) + '.json')
    if not stored.exists():
        raise ValueError('No preserved historical baseline exists for this root.')
    baseline = json.loads(stored.read_text())
    errors = hook.failures(hook.run_scan(root, baseline), baseline)
    if errors:
        raise ValueError('\n'.join(errors))
    print(f'Historical audit passed against the preserved baseline for {root}.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('begin', 'audit'):
        commands.add_parser(action).add_argument('--standalone', action='store_true')
    commands.add_parser('stop')
    verification = commands.add_parser('verify')
    verification.add_argument('--review', required=True)
    verification.add_argument('--docs-only', action='store_true')
    verification.add_argument('--standalone', action='store_true')
    verification.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == 'stop':
        print(json.dumps(stop(json.load(sys.stdin))))
        return
    hook.verify_integrity()
    root = target_root(Path.cwd(), args.standalone)
    if args.action == 'audit':
        audit(root)
        return
    session = os.environ.get('CODEX_SESSION_ID') or os.environ.get('CODEX_THREAD_ID')
    record = registration(session, root)
    if args.action == 'begin':
        begin(root, record)
        print(f'Registered pre-edit baseline for {root}.')
    else:
        if not record.exists():
            parser.error('Missing pre-edit baseline. Preserve the work and report verification incomplete.')
        command = args.command
        if command[:1] == ['--']:
            command = command[1:]
        verify(root, record, args.review, command, args.docs_only)
        print(f'Verified and recorded changed files in {root}.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
