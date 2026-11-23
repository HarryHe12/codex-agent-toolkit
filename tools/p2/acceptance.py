"""Verify documented behavior and representative test sensitivity in scratch copies."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile

from acceptance_recipe import PLAN, load, regular_file


def digest(content):
    return hashlib.sha256(content).hexdigest()


def tracked_paths(root):
    plan = load(root)
    return {name for case in plan[1] for name in case['inputs']} if plan else set()


def selected(cases, differences):
    return [case for case in cases if PLAN in differences or set(case['inputs']).intersection(differences)]


def require_execution_choice(root, differences, authorized):
    plan = load(root)
    if plan and selected(plan[1], differences) and not authorized:
        raise ValueError(f'Review the commands in {PLAN} and rerun verify with --acceptance.')


def fingerprints(root, cases):
    result = {}
    for name in sorted({name for case in cases for name in case['inputs']}):
        path = regular_file(root, name)
        result[name] = [digest(path.read_bytes()), stat.S_IMODE(path.stat().st_mode)]
    return result


def state(root, differences):
    plan = load(root)
    if plan is None:
        return None, []
    content, cases = plan
    active = selected(cases, differences)
    return {'plan_sha256': digest(content), 'inputs': fingerprints(root, active),
            'cases': [case['id'] for case in active]}, active


def materialize(root, case, scratch):
    scratch.mkdir()
    for name in case['inputs']:
        destination = scratch / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(regular_file(root, name), destination)


def run(argv, cwd, deadline):
    with subprocess.Popen(argv, cwd=cwd, text=True, errors='replace',
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=deadline)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        return process.returncode, stdout, stderr


def check_example(case, scratch):
    document = (scratch / case['document']).read_text()
    expected = case['example']['output']
    if case['behavior'] not in document:
        raise ValueError(f"{case['document']} is missing the declared behavior statement.")
    if not any(block == expected for block in documented_examples(document)):
        raise ValueError(f"{case['document']} is missing the expected output in a fenced text example.")
    code, stdout, _ = run(case['example']['argv'], scratch, case['timeout'])
    if code != 0 or stdout.rstrip('\r\n') != expected:
        raise ValueError('The executable example did not produce the documented output.')


def documented_examples(text):
    return re.findall(r'^```text[ \t]*\n(.*?)\n^```[ \t]*$', text, flags=re.MULTILINE | re.DOTALL)


def check_positive(case, scratch):
    test = case['test']
    code, stdout, stderr = run(test['argv'], scratch, case['timeout'])
    if code != 0 or test['success'] not in (stdout + '\n' + stderr).splitlines():
        raise ValueError('The selected test did not pass with its declared success marker.')


def check_counterexample(case, scratch):
    probe = case['counterexample']
    path = scratch / probe['path']
    content = path.read_text()
    if content.count(probe['before']) != 1:
        raise ValueError('Counterexample must match exactly one program fragment in the scratch copy.')
    path.write_text(content.replace(probe['before'], probe['after'], 1))
    code, stdout, stderr = run(case['test']['argv'], scratch, case['timeout'])
    lines = (stdout + '\n' + stderr).splitlines()
    if code == 0:
        raise ValueError('The same test accepted the representative defect.')
    if code != probe['exit_code'] or probe['failure'] not in lines or case['test']['success'] in lines:
        raise ValueError('The counterexample failed for an unverified reason.')


def verified_case(case):
    return {'id': case['id'], 'document': case['document'], 'example': 'passed',
            'test': 'passed' if case['test'] else 'not_configured',
            'counterexample': 'rejected' if case['counterexample'] else 'not_configured'}


def evaluate(root, case):
    with tempfile.TemporaryDirectory(prefix='toolkit-acceptance-') as folder:
        directory = Path(folder)
        example = directory / 'example'
        materialize(root, case, example)
        check_example(case, example)
        if case['test']:
            positive = directory / 'test'
            materialize(root, case, positive)
            check_positive(case, positive)
        if case['counterexample']:
            negative = directory / 'counterexample'
            materialize(root, case, negative)
            check_counterexample(case, negative)
    return verified_case(case)


def verify(root, differences):
    expected, cases = state(root, differences)
    if expected is None:
        return None
    results = []
    for case in cases:
        try:
            results.append(evaluate(root, case))
        except subprocess.TimeoutExpired as error:
            raise ValueError(f'Acceptance {case["id"]}: timed out after {error.timeout}s.') from error
        except (OSError, ValueError) as error:
            raise ValueError(f'Acceptance {case["id"]}: {error}') from error
    if state(root, differences)[0] != expected:
        raise ValueError('Acceptance files or plan changed during verification. Re-run verification.')
    print(json.dumps({'acceptance': 'passed' if cases else 'not_applicable',
                      'state': expected, 'results': results}))
    return {'state': expected, 'results': results}


def require_current(root, differences, receipt):
    current, cases = state(root, differences)
    if not cases:
        return
    stored = receipt if isinstance(receipt, dict) else {}
    if stored.get('state') != current:
        raise ValueError('Current acceptance evidence is missing or stale. Re-run verification.')
    if stored.get('results') != [verified_case(case) for case in cases]:
        raise ValueError('The receipt lacks a passing result for a selected scenario. Re-run verification.')
