"""Read bounded Codex turn evidence without scanning any workspace."""
import ast
import json
from pathlib import Path
import re
import shlex
from urllib.parse import unquote, urlparse


TAIL_BYTES = 4 * 1024 * 1024
WRITE_COMMANDS = {'cp', 'install', 'mkdir', 'mv', 'patch', 'rm', 'rmdir', 'tee', 'touch'}
WRITE_METHOD = re.compile(r'\.(?:write_text|write_bytes|unlink|rename)\s*\(')
PATCH_PATH = re.compile(r'^\*\*\* (?:Add File|Delete File|Update File|Move to): (.+)$', re.M)


def local_path(value):
    if not isinstance(value, str):
        return None
    if value.startswith('file://'):
        url = urlparse(value)
        if url.netloc not in ('', 'localhost'):
            return None
        value = unquote(url.path)
    path = Path(value)
    return path.resolve() if path.is_absolute() else None


def tail(path):
    with path.open('rb') as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - TAIL_BYTES))
        if size > TAIL_BYTES:
            stream.readline()
        for raw in stream:
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def script_of(command):
    vector = command
    if isinstance(command, str):
        try:
            vector = ast.literal_eval(command)
        except (SyntaxError, ValueError):
            return command
    if isinstance(vector, list) and vector and all(isinstance(arg, str) for arg in vector):
        if len(vector) == 3 and vector[1] in ('-c', '-lc'):
            return vector[2]
        return shlex.join(vector)
    return command if isinstance(command, str) else ''


def writes(script):
    if WRITE_METHOD.search(script) or re.search(r'\bopen\([^\n]*,\s*["\'][wax]', script):
        return True
    try:
        shell = shlex.shlex(script, posix=True, punctuation_chars=True)
        shell.whitespace_split = True
        words = list(shell)
    except ValueError:
        return False
    if any(word in ('>', '>>', '>|') for word in words):
        return True
    for segment in re.split(r'(?:\n|;|&&|\|\|)', script):
        try:
            args = shlex.split(segment, comments=True)
        except ValueError:
            continue
        if args and command_writes(args):
            return True
    return False


def command_writes(args):
    command = Path(args[0]).name
    if command in WRITE_COMMANDS or command == 'apply_patch':
        return True
    if command == 'sed' and any(arg.startswith('-i') for arg in args[1:]):
        return True
    if command == 'git':
        return any(arg in ('am', 'apply', 'checkout', 'cherry-pick', 'commit', 'merge',
                           'mv', 'rebase', 'reset', 'restore', 'rm', 'stash') for arg in args[1:2])
    return False


def event_paths(item, cwd):
    locations = set()
    for change in item.get('changes') or ():
        path = change.get('path') if isinstance(change, dict) else change
        if isinstance(path, str):
            locations.add((cwd / path).resolve().parent)
    command = script_of(item.get('command', ''))
    for path in PATCH_PATH.findall(command):
        locations.add((cwd / path).resolve().parent)
    if writes(command):
        locations.add(cwd)
    return locations


def turn_writes(event):
    transcript = local_path(event.get('transcript_path') or '')
    if transcript is None or not transcript.is_file():
        return None
    cwd = local_path(event.get('cwd') or '')
    active_turn = None
    locations = set()
    for row in tail(transcript):
        payload = row.get('payload', {})
        if row.get('type') == 'turn_context' and active_turn != payload.get('turn_id'):
            active_turn = payload.get('turn_id')
            locations.clear()
        if active_turn and cwd and row.get('type') == 'response_item' and payload.get('name') == 'apply_patch':
            locations.update(event_paths({'command': payload.get('input', '')}, cwd))
        if active_turn is None or payload.get('turn_id') != active_turn:
            continue
        item = payload.get('item', {})
        if item.get('type') not in ('CommandExecution', 'PatchApply'):
            continue
        directory = local_path(item.get('cwd') or '') or cwd
        if directory is not None:
            locations.update(event_paths(item, directory))
    return locations if active_turn is not None else None
