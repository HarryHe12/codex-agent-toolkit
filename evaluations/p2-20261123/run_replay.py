"""Compare the first handoff and follow-up on the same fixed P1/P2 proposals."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from zipfile import ZipFile

from scenarios import CASES, baseline, correction, proposal


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = 'Codex-Agent-Toolkit.zip'


def run(args, cwd=None, payload=None):
    env = dict(os.environ)
    env.pop('CODEX_GUARD_NODE', None)
    return subprocess.run(list(map(str, args)), cwd=cwd, input=payload, env=env,
                          capture_output=True, text=True, timeout=240)


def ok(process):
    if process.returncode:
        raise RuntimeError(process.stderr or process.stdout)
    return process.stdout


def digest(content):
    return hashlib.sha256(content).hexdigest()


def installation(place, archive):
    source = place / 'source'
    with ZipFile(archive) as package:
        package.extractall(source)
    command = [sys.executable, source / 'install.py', 'install', '--home', place / 'installed', '--offline']
    return Path(json.loads(ok(run(command)))['guard'])


def write(repo, files):
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def judge(repo, case):
    process = run([sys.executable, 'app.py'], cwd=repo)
    issues = []
    if process.returncode or process.stdout.rstrip('\n') != case.expected:
        issues.append('business_output')
    doc = (repo / 'README.md').read_text()
    if f'```text\n{case.expected}\n```' not in doc or case.behavior not in doc:
        issues.append('documented_example')
    if case.counterexample:
        with TemporaryDirectory(prefix='p2-oracle-') as scratch:
            alternate = Path(scratch)
            write(alternate, {'app.py': f'print({case.counterexample[2]!r})\n',
                              'test_case.py': (repo / 'test_case.py').read_text()})
            negative = run([sys.executable, 'test_case.py'], cwd=alternate)
        if negative.returncode != 1 or 'BUSINESS_INVARIANT_FAILED' not in negative.stderr.splitlines():
            issues.append('weak_regression')
    return issues


def attempt(guard, arm, repo, case):
    command = [sys.executable, guard, '--session', case.name, 'verify', '--repo', repo,
               '--review', 'Checked the proposed behavior and example.']
    if arm == 'p2':
        command.append('--acceptance')
    if case.adjacent:
        command.append('--docs-only')
    else:
        command.extend(('--', sys.executable, 'test_case.py' if case.counterexample else 'smoke.py'))
    started = time.monotonic()
    verified = run(command)
    event = json.dumps({'session_id': case.name, 'cwd': str(repo), 'hook_event_name': 'Stop'})
    stop = json.loads(ok(run([sys.executable, guard, 'hook', 'codex'], payload=event)))
    elapsed = time.monotonic() - started
    passed = verified.returncode == 0 and not stop
    if not passed and stop.get('decision') != 'block':
        raise ValueError(f'{arm} unexpectedly left no block after failed verification of {case.name}.')
    status = next((json.loads(line)['acceptance'] for line in verified.stdout.splitlines()
                   if line.startswith('{"acceptance"')), None)
    diagnostic = verified.stderr.splitlines()[-1] if not passed and verified.stderr else ''
    return {'gate': 'passed' if passed else 'blocked', 'acceptance': status,
            'verify_and_stop_ms': round(elapsed * 1000),
            'diagnostic': diagnostic, 'oracle_issues': judge(repo, case)}


def replay(guard, arm, place, case):
    repo = place / arm / case.name
    repo.mkdir(parents=True)
    write(repo, baseline(case))
    ok(run(['git', 'init', '-qb', 'main'], cwd=repo))
    ok(run(['git', 'add', '.'], cwd=repo))
    ok(run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
            'commit', '-qm', 'Fixed evaluation baseline'], cwd=repo))
    begin = [sys.executable, guard, '--session', case.name, 'begin', '--repo', repo]
    ok(run(begin))
    write(repo, proposal(case))
    first = attempt(guard, arm, repo, case)
    followup = None
    if first['oracle_issues']:
        if first['gate'] == 'passed':
            ok(run(begin))
        write(repo, correction(case))
        followup = attempt(guard, arm, repo, case)
        if followup['gate'] != 'passed' or followup['oracle_issues']:
            raise ValueError(f'Scripted correction failed for {case.name}: {followup}')
    elif first['gate'] != 'passed':
        raise ValueError(f'Unexpected false block in {arm}: {first}')
    return {'first': first, 'after_correction': followup}


def summarize(rows, arm):
    results = [row['arms'][arm] for row in rows]
    initial = [item['first'] for item in results]
    return {'first_guard_accepted': sum(item['gate'] == 'passed' for item in initial),
            'incorrect_first_passes': sum(item['gate'] == 'passed' and bool(item['oracle_issues']) for item in initial),
            'corrections_total': sum(item['after_correction'] is not None for item in results),
            'median_verify_and_stop_ms': statistics.median(item['verify_and_stop_ms'] for item in initial)}


def evaluate(archives):
    with TemporaryDirectory(prefix='p2-workflow-replay-') as folder:
        root = Path(folder)
        guards = {arm: installation(root / arm, archive) for arm, archive in archives.items()}
        rows = []
        for index, case in enumerate(CASES):
            order = ('p1', 'p2') if index % 2 == 0 else ('p2', 'p1')
            arms = {arm: replay(guards[arm], arm, root / 'tasks', case) for arm in order}
            rows.append({'case': case.name, 'request': case.request, 'order': order, 'arms': arms})
    return {'method': 'paired executable workflow replay on frozen synthetic proposals',
            'model_usage': 'not measured', 'operator_effort': 'not measured',
            'environment': {'platform': platform.system(), 'python': platform.python_version(),
                            'node': ok(run(['node', '--version'])).strip()},
            'archive_sha256': {arm: digest(path.read_bytes()) for arm, path in archives.items()},
            'cases': rows, 'summary': {arm: summarize(rows, arm) for arm in archives}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    archives = {arm: ROOT / 'releases' / f'2026-11-23-{arm}' / ARCHIVE for arm in ('p1', 'p2')}
    result = evaluate(archives)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
