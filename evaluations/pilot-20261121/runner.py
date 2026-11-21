"""Paired isolated Codex runs. Auth is temporary and never included in evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import time
from fixtures import COMMON, cases, HOLDOUTS
from grade import grade

HERE = Path(__file__).resolve().parent
LIVE = Path('/Users/a0000/.codex')
MODEL = 'gpt-6.1-sol'
SKILLS = ['local-code-guard', 'instruction-maintenance', 'project-verification', 'prevent-repeat-errors', 'evaluate-agent-skills']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def localize(text, home):
    return text.replace(str(LIVE), str(home))


def prepare_engine(home):
    engine = home/'code-guard'
    for name in ['hook.py', 'scan.mjs', 'python_metrics.py', 'policies.json']:
        write(engine/name, localize((LIVE/'code-guard'/name).read_text(), home))
    (engine/'node_modules').symlink_to(LIVE/'code-guard/node_modules', target_is_directory=True)
    for name in ['md_gate.py', 'md_rules.py', 'md_store.py', 'md_write.py']:
        write(home/'md-governance'/name, localize((LIVE/'md-governance'/name).read_text(), home))
    for name in SKILLS:
        write(home/'skills'/name/'SKILL.md', localize((LIVE/'skills'/name/'SKILL.md').read_text(), home))
    command = '/usr/bin/python3 '+str(home/'wrapper.py')
    hooks = {kind: [{'matcher': '.*', 'hooks': [{'type': 'command', 'command': command, 'timeout': 120}]}] for kind in ['SessionStart', 'PreToolUse', 'PostToolUse', 'Stop', 'SubagentStop']}
    write(home/'hooks.json', json.dumps({'hooks': hooks}))
    wrapper = 'import json,subprocess,sys\nfrom pathlib import Path\ne=json.load(sys.stdin)\np=subprocess.run(["/usr/bin/python3",'+repr(str(engine/'hook.py'))+'],input=json.dumps(e),text=True,capture_output=True,check=True)\no=json.loads(p.stdout)\nwith Path('+repr(str(home/'hook-events.jsonl'))+').open("a") as f:f.write(json.dumps({"kind":e["hook_event_name"],"outcome":o})+"\\n")\nprint(p.stdout)\n'
    write(home/'wrapper.py', wrapper)
    protected = [p for folder in [engine, home/'md-governance'] for p in folder.glob('*') if p.is_file()]
    protected += [home/'hooks.json', home/'skills/local-code-guard/SKILL.md']
    write(engine/'integrity.json', json.dumps({str(p): sha(p) for p in protected}))


def prepare_home(home, variant):
    home.mkdir(mode=0o700)
    shutil.copyfile(LIVE/'auth.json', home/'auth.json')
    (home/'auth.json').chmod(0o600)
    config = f'model = "{MODEL}"\nmodel_reasoning_effort = "low"\napproval_policy = "never"\nsandbox_mode = "workspace-write"\n[features]\nhooks = '+str(variant == 'B').lower()+'\nshell_snapshot = false\n'
    write(home/'config.toml', config)
    if variant == 'B':
        prepare_engine(home)
        write(home/'AGENTS.md', 'Use local-code-guard for code edits and instruction-maintenance for persistent instruction edits. Read project-verification when a working product verification entry is missing. Use prevent-repeat-errors for evidenced recurring failures. Use evaluate-agent-skills when evaluating skills. Keep changes scoped to the request.\n')


def materialize(case, root):
    root.mkdir()
    for name, content in case['files'].items():
        write(root/name, content)
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'seed'], check=True)


def events_summary(text):
    events = []
    for line in text.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    completed = [e for e in events if e.get('type') == 'turn.completed']
    items = [e.get('item', {}) for e in events if e.get('type') == 'item.completed']
    return {'usage': completed[-1].get('usage') if completed else None,
            'tool_calls': sum(i.get('type') in ['command_execution', 'file_change', 'mcp_tool_call'] for i in items),
            'last_message': '\n'.join(i.get('text', '') for i in items if i.get('type') == 'agent_message')}


def execute(case, variant, ordinal, workspace):
    run = workspace/f'case-{ordinal:02d}'
    run.mkdir()
    home = run/'agent-home'
    root = run/'project'
    prepare_home(home, variant)
    materialize(case, root)
    command = [shutil.which('codex'), '--no-daemon', 'exec', '--ephemeral', '--json', '--dangerously-bypass-hook-trust', '-C', str(root), '--add-dir', str(home), COMMON+case['task']]
    env = dict(os.environ, CODEX_HOME=str(home))
    env.pop('OPENAI_API_KEY', None)
    env.pop('CODEX_API_KEY', None)
    start = time.monotonic()
    try:
        process = subprocess.run(command, env=env, text=True, capture_output=True, timeout=360)
        text, stderr, code = process.stdout, process.stderr, process.returncode
    except subprocess.TimeoutExpired as error:
        text = (error.stdout or b'').decode() if isinstance(error.stdout, bytes) else error.stdout or ''
        stderr, code = 'TIMEOUT 360s', None
    duration = time.monotonic()-start
    (home/'auth.json').unlink(missing_ok=True)
    result = {'task': case['id'], 'variant': variant, 'ordinal': ordinal, 'seconds': round(duration, 3), 'exit_code': code, **events_summary(text), 'grade': grade(case, root)}
    hooks = home/'hook-events.jsonl'
    result['hook_events'] = [json.loads(line) for line in hooks.read_text().splitlines()] if hooks.exists() else []
    snapshot = {str(p.relative_to(root)): p.read_text() for p in root.rglob('*') if p.is_file() and '.git' not in p.parts}
    destination = HERE/'runs'/f'{ordinal:02d}'
    write(destination/'result.json', json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    write(destination/'events.jsonl', text)
    write(destination/'stderr.txt', stderr)
    write(destination/'submission.json', json.dumps(snapshot, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: result[k] for k in ['task', 'variant', 'seconds', 'exit_code', 'grade', 'tool_calls', 'usage']}, ensure_ascii=False), flush=True)
    return result


def freeze():
    inputs = cases()
    manifest = {'model': MODEL, 'reasoning': 'low', 'timeout_seconds': 360, 'tool_budget': '30 calls instructed equally; actual calls reported', 'comparison': 'ablation control A vs installed five skills plus repaired gates B; not exact historical replay', 'common_prompt': COMMON, 'cases': inputs, 'holdouts_not_executed': HOLDOUTS,
                'sources': {str(p.relative_to(LIVE)): sha(p) for p in [LIVE/'code-guard/hook.py', LIVE/'code-guard/scan.mjs']+[LIVE/'skills'/name/'SKILL.md' for name in SKILLS]},
                'harness': {p.name: sha(p) for p in HERE.glob('*.py')}}
    write(HERE/'protocol.json', json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    return inputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=12)
    args = parser.parse_args()
    inputs = freeze()
    workspace = Path(tempfile.mkdtemp(prefix='agent-pair-', dir='/tmp'))
    workspace.chmod(0o700)
    write(HERE/'workspace.txt', str(workspace)+'\n')
    schedule = []
    rng = random.Random(20261121)
    for case in inputs:
        variants = ['A', 'B']
        rng.shuffle(variants)
        schedule.extend((case, variant) for variant in variants)
    results = []
    for ordinal, (case, variant) in enumerate(schedule[:args.limit], 1):
        results.append(execute(case, variant, ordinal, workspace))
        write(HERE/'results.json', json.dumps(results, ensure_ascii=False, indent=2)+'\n')
        if results[-1]['usage'] is None:
            raise RuntimeError('No completed model turn; inspect execution environment before more runs')
    print('COMPLETE', flush=True)


if __name__ == '__main__':
    main()
