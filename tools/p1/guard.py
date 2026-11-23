"""Shared CLI for local edits, native hook adapters and committed Git changes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import hosts


def engine(node):
    if node:
        os.environ['CODEX_GUARD_NODE'] = node
    directory = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(directory / 'code-guard'))
    import closeout
    return closeout


def parser_for_cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node')
    parser.add_argument('--session', help='The native session ID or a user-selected standalone task ID.')
    commands = parser.add_subparsers(dest='action', required=True)
    for name in ('begin', 'verify'):
        command = commands.add_parser(name)
        command.add_argument('--repo', type=Path, default=Path.cwd())
        command.add_argument('--standalone', action='store_true')
        if name == 'verify':
            command.add_argument('--review', required=True)
            command.add_argument('--docs-only', action='store_true')
            command.add_argument('command', nargs=argparse.REMAINDER)
    ci = commands.add_parser('check')
    ci.add_argument('--repo', type=Path, default=Path.cwd())
    ci.add_argument('--base', required=True)
    ci.add_argument('--head', required=True)
    native = commands.add_parser('hook')
    native.add_argument('host', choices=hosts.EVENTS)
    commands.add_parser('smoke')
    return parser


def task_action(closeout, args):
    root = closeout.target_root(args.repo, args.standalone)
    session = args.session or os.environ.get('CODEX_SESSION_ID') or os.environ.get('CODEX_THREAD_ID')
    path = closeout.registration(session, root)
    if args.action == 'begin':
        closeout.begin(root, path)
        print(f'Registered pre-edit baseline for {root}.')
        return
    if not path.is_file():
        raise ValueError('Missing pre-edit baseline. Preserve the edits and report verification incomplete.')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    closeout.verify(root, path, args.review, command, args.docs_only)
    print(f'Verified current changed files in {root}.')


def smoke(closeout):
    with tempfile.TemporaryDirectory(prefix='toolkit-smoke-') as directory:
        root = Path(directory)
        fixture = root / 'fixture.js'
        fixture.write_text('const value = 1;\n')
        before = closeout.hook.run_scan(root, {}, source_only=True)
        if set(before) != {'fixture.js'} or before['fixture.js']['metrics']:
            raise ValueError('Scanner did not accept the expected clean fixture.')
        fixture.write_text('new Buffer(1);\n')
        after = closeout.hook.run_scan(root, before, source_only=True)
        if not any('Buffer' in item for item in closeout.hook.failures(after, before)):
            raise ValueError('Scanner did not reject the expected deprecated API fixture.')
    print('Smoke check passed: accepted a clean file and rejected a deprecated API.')


def native_action(closeout, host):
    event = json.load(sys.stdin)
    try:
        kind = event.get('hook_event_name')
        if kind not in hosts.EVENTS[host]:
            raise ValueError(f'Unexpected {host} hook event: {kind!r}')
        if kind == 'SessionStart':
            result = hosts.launch_context(event, Path(__file__).resolve(), sys.executable, closeout.hook.NODE)
        else:
            result = closeout.stop(hosts.normalized_stop(host, event))
    except (AttributeError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        result = {'systemMessage': f'Agent Toolkit verification incomplete: {error}'}
    print(json.dumps(hosts.host_result(host, result)))


def main():
    args = parser_for_cli().parse_args()
    try:
        closeout = engine(args.node)
        if args.action == 'hook':
            native_action(closeout, args.host)
            return 0
        closeout.hook.verify_integrity()
        if args.action == 'check':
            from changes import assess
            result = assess(args.repo.resolve(), args.base, args.head)
            print(json.dumps(result, ensure_ascii=False))
            return int(result['status'] == 'failed')
        if args.action == 'smoke':
            smoke(closeout)
        else:
            task_action(closeout, args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        if args.action == 'check':
            detail = error.stderr.decode(errors='replace').strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
            print(json.dumps({'status': 'unavailable', 'error': detail or str(error)}))
        else:
            print(f'Agent Toolkit: {error}', file=sys.stderr)
            if args.action == 'hook':
                print(json.dumps({'systemMessage': f'Agent Toolkit verification incomplete: {error}'}))
                return 0
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
