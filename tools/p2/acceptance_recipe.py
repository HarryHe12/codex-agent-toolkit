"""Load a project's opt-in behavior checks using literal worktree paths."""
import json
from pathlib import Path, PurePosixPath


PLAN = '.agent-toolkit/acceptance.json'


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError(f'{label} must be nonempty text.')
    return value


def relative(value, label):
    name = nonempty(value, label)
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or '..' in path.parts or path.as_posix() != name:
        raise ValueError(f'{label} must be a normalized path relative to the worktree.')
    if {'.git', 'node_modules', '.venv', 'venv', '__pycache__'}.intersection(path.parts):
        raise ValueError(f'{label} cannot refer to Git metadata or dependency directories.')
    return name


def regular_file(root, name):
    path = root / name
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError(f'Acceptance requires a regular worktree file: {name}.')
    return path


def command(value, label):
    if not isinstance(value, list) or not value:
        raise ValueError(f'{label} must be a nonempty argv array.')
    return [nonempty(arg, label) for arg in value]


def test_config(case, inputs, document):
    raw = case.get('test')
    if raw is None:
        if 'counterexample' in case:
            raise ValueError('A counterexample requires a test command and success marker.')
        return None, None
    test = {'argv': command(raw['argv'], 'test.argv'),
            'success': nonempty(raw['success'], 'test.success')}
    probe = case.get('counterexample')
    if probe is None:
        return test, None
    path = relative(probe['path'], 'counterexample.path')
    if path not in inputs or path == document:
        raise ValueError('Counterexample path must be a declared program input.')
    before = nonempty(probe['before'], 'counterexample.before')
    after = nonempty(probe['after'], 'counterexample.after')
    exit_code = probe.get('exit_code', 1)
    if after == before or type(exit_code) is not int or not 1 <= exit_code <= 125:
        raise ValueError('Counterexample requires a changed value and an ordinary nonzero test exit code.')
    failure = nonempty(probe['failure'], 'counterexample.failure')
    if failure == test['success']:
        raise ValueError('The counterexample failure and test success markers must differ.')
    return test, {'path': path, 'before': before, 'after': after,
                  'failure': failure, 'exit_code': exit_code}


def scenario(raw):
    identifier = nonempty(raw['id'], 'scenario.id')
    values = raw['inputs']
    if not isinstance(values, list) or not values:
        raise ValueError('inputs must be a nonempty array of regular worktree files.')
    inputs = {relative(value, 'inputs') for value in values}
    document = relative(raw['document'], 'scenario.document')
    timeout = raw.get('timeout_seconds', 30)
    if type(timeout) is not int or timeout <= 0:
        raise ValueError('timeout_seconds must be a positive whole number.')
    test, counterexample = test_config(raw, inputs, document)
    example = raw['example']
    return {'id': identifier, 'inputs': sorted(inputs | {document}), 'document': document,
            'behavior': nonempty(raw['behavior'], 'scenario.behavior'),
            'example': {'argv': command(example['argv'], 'example.argv'),
                        'output': nonempty(example['output'], 'example.output')},
            'test': test, 'counterexample': counterexample, 'timeout': timeout}


def load(root):
    plan = root / PLAN
    if not plan.exists() and not plan.is_symlink():
        return None
    raw = regular_file(root, PLAN).read_bytes()
    try:
        definition = json.loads(raw)
        cases = definition['scenarios']
        if type(definition['version']) is not int or definition['version'] != 1 or not isinstance(cases, list):
            raise ValueError('Expected version 1 and a scenarios array.')
        parsed = [scenario(item) for item in cases]
        if not parsed or len({item['id'] for item in parsed}) != len(parsed):
            raise ValueError('Declare at least one scenario with unique IDs.')
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f'Invalid acceptance plan {PLAN}: {error}') from error
    return raw, parsed
