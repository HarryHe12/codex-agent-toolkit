"""Independent native Codex session: isolated rules, no author conversation or tools."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from md_verdict import policy,schema
from md_progress import ReviewFailure, progress, run_reviewer


def prepare(home):
    live=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    auth=live/'auth.json'
    if not auth.exists():
        raise ReviewFailure('authentication', 'Independent reviewer requires an existing Codex CLI login.')
    shutil.copyfile(auth,home/'auth.json'); (home/'auth.json').chmod(0o600)
    text=(live/'config.toml').read_text() if (live/'config.toml').exists() else ''
    model=re.search(r'^model\s*=\s*"([^"]+)"',text,re.M)
    setting=('model = '+json.dumps(model[1])+'\n') if model else ''
    (home/'config.toml').write_text(configuration(setting))
    return model[1] if model else 'account default'


def configuration(setting=''):
    return setting+'approval_policy = "never"\nsandbox_mode = "read-only"\nweb_search = "disabled"\n[features]\nhooks = false\nshell_snapshot = false\nshell_tool = false\nunified_exec = false\napps = false\nplugins = false\nmulti_agent = false\nview_image = false\ngoals = false\n'

def parse_events(text):
    rows=[]
    for line in text.splitlines():
        try: rows.append(json.loads(line))
        except ValueError: continue
    items=[row.get('item',{}) for row in rows if row.get('type') in ('item.started','item.completed')]
    warnings=[]
    for item in items:
        if item.get('type') in ('agent_message','reasoning'): continue
        message=item.get('message','')
        if item.get('type')=='error' and message.startswith('Model metadata for `') and 'Defaulting to fallback metadata' in message:
            warnings.append(message)
            continue
        raise ValueError('Independent reviewer attempted tools or failed: '+str(item.get('type')))
    completed=[row for row in rows if row.get('type')=='turn.completed']
    if not completed: raise ValueError('Independent reviewer did not complete.')
    return completed[-1].get('usage',{}),warnings


def write_failure_trace(destination, result, started):
    allowed_events = {'thread.started', 'turn.started', 'turn.completed', 'turn.failed',
                      'item.started', 'item.completed', 'item.updated', 'error'}
    events = {}
    for line in (result.stdout or '').splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        event = row.get('type') if isinstance(row, dict) else None
        if isinstance(event, str) and event in allowed_events:
            events[event] = events.get(event, 0) + 1
    stderr = result.stderr or ''
    signals = {
        'connection_refused': r'connection refused|ECONNREFUSED',
        'authentication': r'unauthorized|not logged in|\b401\b|authentication',
        'permission': r'forbidden|permission denied|\b403\b',
        'timeout': r'timed? ?out|timeout',
        'transport': r'connection|network|websocket|stream disconnected',
        'configuration': r'unexpected argument|invalid config|error loading config',
        'input_too_large': r'input_too_large|exceeds the maximum length',
    }
    trace = {
        'stage': 'reviewer_process',
        'returncode': result.returncode,
        'seconds': round(time.monotonic() - started, 3),
        'events': events,
        'stderr_lines': len(stderr.splitlines()),
        'signals': [name for name, pattern in signals.items() if re.search(pattern, stderr, re.I)],
    }
    for field in ('max_chars', 'actual_chars'):
        match = re.search(r'"' + field + r'"\s*:\s*(\d+)', stderr)
        if match:
            trace[field] = int(match[1])
    (destination/'failure-trace.json').write_text(json.dumps(trace, indent=2) + chr(10))
    return trace


def review_schema(clause_ids):
    result = schema()
    clauses = result['properties']['clauses']
    clauses['description'] = f'Exactly {len(clause_ids)} added-clause judgments, one for each listed ID.'
    if clause_ids:
        clauses['items']['properties']['id']['enum'] = clause_ids
    return result


def invoke(packet,destination):
    executable=shutil.which('codex')
    if not executable:
        raise ReviewFailure('missing_dependency', 'Install or expose the Codex CLI, then rerun review --retry.')
    destination.mkdir(parents=True,mode=0o700,exist_ok=True)
    clause_ids = [clause['id'] for clause in packet['added']]
    prompt=json.dumps({'role':'Independent instruction reviewer. Return JSON only; no tools.',
                       'output_requirement': f'In clauses return exactly {len(clause_ids)} judgments, one per supplied ID. '
                       'Include non-pass judgments. Exclude removed and unchanged clauses. Assess removal and preservation in dimensions.',
                       'required_clause_ids': clause_ids, 'policy':policy(),'review_input':packet},ensure_ascii=False)
    max_chars = 1048576
    if len(prompt) > max_chars:
        raise ReviewFailure('input_too_large', f'Independent review input has {len(prompt)} characters '
                            f'(CLI limit: {max_chars}); narrow the candidate or explicit evidence.')
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='md-review-') as directory:
        home=Path(directory); model=prepare(home)
        output=home/'verdict.json'; schema_path=home/'schema.json'
        schema_path.write_text(json.dumps(review_schema(clause_ids)))
        command=[executable,'--no-daemon','exec','--ephemeral','--skip-git-repo-check','--json',
                 '-C',str(home),'-s','read-only','--output-schema',str(schema_path),'-o',str(output),'-']
        env=dict(os.environ,CODEX_HOME=str(home))
        env.pop('OPENAI_API_KEY',None); env.pop('CODEX_API_KEY',None)
        try:
            result=run_reviewer(command,prompt,env,destination)
            verdict,usage,warnings=read_result(result,output,destination,started)
            (destination/'events.jsonl').write_text(result.stdout)
        except subprocess.TimeoutExpired as error:
            failed = subprocess.CompletedProcess(command, None, error.stdout, error.stderr)
            trace = write_failure_trace(destination, failed, started)
            raise ReviewFailure('timeout', f"Independent reviewer timed out after {trace['seconds']}s. "
                                f'Diagnostics: {destination / "failure-trace.json"}') from error
        finally:
            (home/'auth.json').unlink(missing_ok=True)
    execution={'provider':'native Codex CLI','model':model,'usage':usage,'seconds':round(time.monotonic()-started,3),
               'isolated_conversation':True,'tools_used':False,'returncode':result.returncode,'runtime_warnings':warnings}
    return verdict,execution


def read_result(result, output, destination, started):
    try:
        if result.returncode or not output.exists():
            raise ValueError('Independent reviewer did not produce a result.')
        usage, warnings = parse_events(result.stdout)
        return json.loads(output.read_text()), usage, warnings
    except ValueError as error:
        trace = write_failure_trace(destination, result, started)
        reasons = set(trace['signals'])
        order = ('authentication', 'permission', 'configuration', 'input_too_large',
                 'timeout', 'connection_refused', 'transport')
        reason = next((name for name in order if name in reasons), 'invalid_response')
        location = destination / 'failure-trace.json'
        progress(f'Independent reviewer returned {reason}. Diagnostics: {location}')
        raise ReviewFailure(reason, f'{error} Diagnostics: {location}') from error
