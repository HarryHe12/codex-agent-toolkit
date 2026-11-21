"""Independent contract checks; never shown to candidates."""
import json
import re
import subprocess
from pathlib import Path


def call(root, payload):
    result = subprocess.run(['node', 'cli.js', json.dumps(payload)], cwd=root, capture_output=True, text=True, timeout=8)
    if result.returncode:
        return False, result.stderr.strip()
    try:
        return True, json.loads(result.stdout)
    except ValueError:
        return False, result.stdout


def normalization(root):
    checks = []
    for action, status in [('find', 'open'), ('cancel', 'cancelled'), ('archive', 'archived')]:
        for identity in ['A1', ' A1 ', '\tA1\n']:
            ok, out = call(root, {'action': action, 'id': identity})
            checks.append(ok and out == {'id': 'A1', 'status': status})
        for identity in ['', '  ', None, 1, [], {}]:
            checks.append(not call(root, {'action': action, 'id': identity})[0])
    checks.append(call(root, {'action': 'find', 'id': 'a1'}) == (True, None))
    checks.append(call(root, {'action': 'find', 'id': 'Z'}) == (True, None))
    checks.append(not call(root, {'action': 'cancel', 'id': 'Z'})[0])
    return checks


def transfer(root):
    state = {'stock': {'A': 10, 'B': 2}, 'done': []}
    request = {'id': 'r1', 'from': 'A', 'to': 'B', 'units': 3, 'actor': {'role': 'manager', 'stores': ['A', 'B']}}
    ok, out = call(root, {'state': state, 'request': request})
    expected = {'stock': {'A': 7, 'B': 5}, 'done': ['r1']}
    checks = [ok and out == {'state': expected, 'ok': True}]
    checks.append(call(root, {'state': expected, 'request': request}) == (True, {'state': expected, 'ok': True}))
    changes = [{'units': v} for v in [0, -1, 1.5, 11, '3']]
    changes += [{'actor': {'role': 'staff', 'stores': ['A', 'B']}}, {'actor': {'role': 'manager', 'stores': ['A']}}]
    changes += [{'from': 'Z'}, {'to': 'A'}, {'id': ''}, {'actor': None}]
    for change in changes:
        ok, out = call(root, {'state': state, 'request': dict(request, **change)})
        checks.append(ok and isinstance(out, dict) and out.get('state') == state and out.get('ok') is False and bool(out.get('error')))
    check = "const a=require('./app');const s={stock:{A:10,B:2},done:[]};const before=JSON.stringify(s);a.run({state:s,request:"+json.dumps(request)+"});if(JSON.stringify(s)!==before)process.exit(1)"
    checks.append(subprocess.run(['node', '-e', check], cwd=root, capture_output=True, timeout=8).returncode == 0)
    return checks


def dispatch(root):
    checks = [call(root, {'kind': f'type{i}', 'x': x}) == (True, x*(i+1)) for i in range(30) for x in [-2, 0, 1.25]]
    checks += [call(root, {'kind': 'bonus', 'x': x}) == (True, x*2+5) for x in [-2, 0, 1.25]]
    checks += [not call(root, x)[0] for x in [{'kind': 'bad', 'x': 2}, {'kind': 'type0', 'x': '2'}, {'kind': 'bonus', 'x': None}]]
    return checks


def migration(root):
    checks = [call(root, {'action': 'encode', 'text': text}) == (True, expected) for text, expected in [('', ''), ('abc', 'YWJj'), ('你好', '5L2g5aW9')]]
    checks += [call(root, {'action': 'isArray', 'value': v}) == (True, expected) for v, expected in [([], True), ({}, False), ('x', False), (None, False)]]
    checks += [call(root, {'action': 'exists', 'path': str(root/'app.js')}) == (True, True), call(root, {'action': 'exists', 'path': str(root/'absent')}) == (True, False)]
    checks.append(not call(root, {'action': 'bad'})[0])
    code = '\n'.join(p.read_text() for p in root.rglob('*.js') if 'node_modules' not in p.parts)
    checks.append(not re.search(r"new\s+Buffer\s*\(|require\(['\"](?:node:)?sys['\"]\)|\bfs\.exists\s*\(", code))
    return checks


def instructions(root):
    agents = (root/'AGENTS.md').read_text()
    spec = (root/'spec.md').read_text()
    authorization = bool(re.search(r'explicit.*(?:authoriz|approv)|明确.*(?:授权|批准)', agents, re.I))
    role = bool(re.search(r'only\s+managers|仅.*manager|只有.*manager|only.*manager|manager.*才能', spec, re.I))
    retry_lines = [line for line in spec.splitlines() if re.search(r'retr|重试', line, re.I)]
    return [authorization, role, bool(re.search(r'assign|授权', spec, re.I)), bool(re.search(r'audit.*(?:user|actor)|审计.*(?:用户|操作人)', spec, re.I)),
            len(retry_lines) == 1 and bool(re.search(r'three|3|三', retry_lines[0])), not bool(re.search(r'retr|重试|manager', agents, re.I)), '(spec.md)' in agents]


def small(root):
    return [call(root, {}) == (True, {'title': 'Stockroom', 'ready': True})]


GRADERS = {'normalize': normalization, 'transfer': transfer, 'dispatch': dispatch, 'migration': migration, 'instructions': instructions, 'small': small}


def grade(case, root):
    try:
        checks = GRADERS[case['id']](root)
        notes = case['files'].get('notes.txt')
        checks.append((root/'notes.txt').read_text() == notes)
        changed = [str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and '.git' not in p.parts and case['files'].get(str(p.relative_to(root))) != p.read_text()]
        if case['id'] == 'small':
            checks.append(changed == ['app.js'])
        return {'passed': sum(checks), 'total': len(checks), 'all_pass': all(checks), 'changed_files': sorted(changed), 'checks': checks}
    except Exception as error:
        return {'all_pass': False, 'error': str(error)}
