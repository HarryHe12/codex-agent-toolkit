"""Apply the same frozen structural rules to both arms after execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
BASE = Path('/Users/a0000/.codex/code-guard')
NODE = '/Users/a0000/.nvm/versions/node/v24.22.0/bin/node'


def scan(root):
    p = subprocess.run([NODE, str(BASE/'scan.mjs')], input=json.dumps({'root': str(root)}), text=True, capture_output=True, timeout=90, check=True)
    return {key: row for key, row in json.loads(p.stdout).items() if not key.startswith('@md:')}


def materialize(files, root):
    for name, content in files.items():
        target = root/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def quality(case, submission, guard):
    with tempfile.TemporaryDirectory(prefix='quality-audit-') as directory:
        root = Path(directory)
        before, after = root/'before', root/'after'
        before.mkdir()
        after.mkdir()
        materialize(case['files'], before)
        materialize(submission, after)
        initial, final = scan(before), scan(after)
        diagnostics = guard.failures(final, initial)
        active = {name: row['metrics'] for name, row in final.items() if row['metrics']}
        return {'new_or_worsened': diagnostics, 'remaining': active}


def main():
    protocol = json.loads((HERE/'protocol.json').read_text())
    for name, expected in protocol['sources'].items():
        source = Path('/Users/a0000/.codex')/name
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Installed treatment changed during evaluation: '+name)
    spec = importlib.util.spec_from_file_location('frozen_guard', BASE/'hook.py')
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    cases = {case['id']: case for case in protocol['cases']}
    results = json.loads((HERE/'results.json').read_text())
    for row in results:
        folder = HERE/'runs'/f"{row['ordinal']:02d}"
        submission = json.loads((folder/'submission.json').read_text())
        row['quality'] = quality(cases[row['task']], submission, guard)
        stops=[e['outcome'] for e in row['hook_events'] if e['kind']=='Stop']
        row['final_gate_clear']=stops[-1]=={} if stops else None
        row['stop_blocks'] = sum(e['kind'] == 'Stop' and e['outcome'].get('decision') == 'block' for e in row['hook_events'])
        row['post_feedback'] = sum(e['kind'] == 'PostToolUse' and bool(e['outcome']) for e in row['hook_events'])
    (HERE/'analysis.json').write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
    for variant in ['A', 'B']:
        arm = [row for row in results if row['variant'] == variant]
        print(json.dumps({'variant': variant, 'tasks': len(arm), 'contract_pass': sum(row['grade']['all_pass'] for row in arm),
                          'new_quality_failure_tasks': sum(bool(row['quality']['new_or_worsened']) for row in arm),
                          'seconds': sum(row['seconds'] for row in arm), 'tool_calls': sum(row['tool_calls'] for row in arm),
                          'input_tokens': sum(row['usage']['input_tokens'] for row in arm), 'output_tokens': sum(row['usage']['output_tokens'] for row in arm)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
