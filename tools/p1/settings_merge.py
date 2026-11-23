"""Plan idempotent hook settings updates and retain reversible byte snapshots."""
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


def read_bytes(path):
    return path.read_bytes() if path.exists() else None


def dump(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def atomic_write(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    file, name = tempfile.mkstemp(dir=path.parent, prefix='.agent-toolkit-')
    try:
        with os.fdopen(file, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def replace_groups(groups, commands):
    kept = []
    for group in groups:
        entries = group['hooks']
        hooks = [entry for entry in entries if entry.get('command') not in commands]
        if hooks or not entries:
            kept.append(dict(group, hooks=hooks))
    return kept


def configured(raw):
    original = json.loads(raw) if raw is not None else {}
    if not isinstance(original, dict) or not isinstance(original.get('hooks', {}), dict):
        raise ValueError('Expected a JSON object with an optional hooks object.')
    return original


def merged_config(raw, wanted, previous, legacy):
    original = configured(raw)
    command = next(iter(wanted.values()))['hooks'][0]['command']
    hooks = original.get('hooks', {})
    commands = {command, *previous, *legacy} - {None}
    seen = {entry.get('command') for groups in hooks.values() for group in groups for entry in group['hooks']}
    missing = set(legacy) - seen
    if missing and command not in seen:
        raise ValueError('A selected legacy command was not found in the target configuration.')
    replacement = {}
    for event, groups in hooks.items():
        kept = replace_groups(groups, commands)
        if kept or not groups:
            replacement[event] = kept
    for event, group in wanted.items():
        replacement.setdefault(event, []).append(group)
    updated = dict(original, hooks=replacement)
    owner = next((item for item in previous if item and item in seen), None)
    return (raw if original == updated else dump(updated)), command, owner


def journal_path(home, config):
    key = hashlib.sha256(os.fsencode(config)).hexdigest()[:24]
    return home / 'state' / (key + '.json')


def load_journal(path):
    raw = read_bytes(path)
    return json.loads(raw) if raw is not None else {}


def plan(home, config, wanted, legacy):
    journal = journal_path(home, config)
    prior = load_journal(journal)
    current = read_bytes(config)
    after, command, owner = merged_config(current, wanted, (prior.get('command'), prior.get('previous')), legacy)
    mode = stat.S_IMODE(config.stat().st_mode) if current is not None else 0o600
    return {'path': config, 'journal': journal, 'before': current, 'after': after,
            'mode': mode, 'command': command, 'previous': owner, 'events': list(wanted)}


def activate(update):
    config = update['path']
    before, after = update['before'], update['after']
    if read_bytes(config) != before:
        raise ValueError('The host configuration changed during install. Re-run plan.')
    if before == after:
        return
    record = {'path': str(config), 'mode': update['mode'], 'command': update['command'],
              'previous': update['previous'],
              'before': base64.b64encode(before).decode() if before is not None else None,
              'after': hashlib.sha256(after).hexdigest()}
    atomic_write(update['journal'], dump(record))
    atomic_write(config, after, update['mode'])


def rollback(home, config):
    journal = journal_path(home, config)
    record = load_journal(journal)
    if not record or record['path'] != str(config) or 'after' not in record:
        raise ValueError('No recorded Toolkit settings change to roll back for this path.')
    current = read_bytes(config)
    before = base64.b64decode(record['before']) if record['before'] is not None else None
    if current != before:
        if current is None or hashlib.sha256(current).hexdigest() != record['after']:
            raise ValueError('The host configuration changed since installation. Preserve those edits.')
        if before is None:
            config.unlink()
        else:
            atomic_write(config, before, record['mode'])
    if record.get('previous'):
        atomic_write(journal, dump({'path': str(config), 'command': record['previous']}))
    else:
        journal.unlink()
