"""Exercise real npm installation and same-session recovery for both hook variants."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile

from update_guard_dependency_recovery import update_closeout, update_hook


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'releases/2026-11-23-dependency-recovery/Codex-独立审查升级源码包.zip'
LIVE = Path.home() / '.codex/code-guard'
NODE = shutil.which('node')


def run(args, project, env, payload=None):
    return subprocess.run(args, cwd=project, env=env, input=payload, text=True,
                          capture_output=True, timeout=120)


def success(result):
    assert result.returncode == 0, result.stderr
    return result.stdout


def invoke(engine, project, env, kind, **fields):
    event = dict(cwd=str(project), hook_event_name=kind,
                 session_id=env['CODEX_SESSION_ID'], **fields)
    result = run([sys.executable, str(engine / 'hook.py')], project, env, json.dumps(event))
    return json.loads(success(result))


def install_from_diagnostic(diagnostic, engine, project, env):
    command = next(line for line in diagnostic.splitlines() if line.startswith('cd '))
    assert str(engine) in command
    assert ' ci --omit=optional --ignore-scripts --no-audit --no-fund' in command
    assert '继续' in diagnostic and 'current task' in diagnostic
    success(run(['/bin/sh', '-c', command], project, env))
    assert (engine / 'node_modules/eslint/package.json').is_file()


def local_engine(engine):
    engine.mkdir()
    for name in ('scan.mjs', 'policies.json', 'python_metrics.py', 'document_hook.py',
                 'document-project.json', 'package.json', 'package-lock.json'):
        shutil.copy2(LIVE / name, engine / name)
    hook = os.environ.get('GUARD_RECOVERY_ORIGINAL')
    old = Path(hook) if hook else LIVE
    source = (old / 'hook.py').read_text()
    if 'class ScannerDependencyError' not in source:
        source = update_hook(source, local=True)
    (engine / 'hook.py').write_text(source)
    source = (old / 'closeout.py').read_text()
    if 'hook.check_dependencies' not in source:
        source = update_closeout(source)
    (engine / 'closeout.py').write_text(source)
    (engine.parent / 'hooks.json').write_text('{"hooks":{"Stop":[]}}')


def local_flow(engine, project, env):
    closeout = [sys.executable, str(engine / 'closeout.py')]
    success(run([*closeout, 'begin'], project, env))
    (project / 'app.js').write_text('console.log(2);\n')
    (project / 'CHANGE.md').write_text('The program now prints 2.\n')
    shutil.rmtree(engine / 'node_modules/eslint')
    blocked = invoke(engine, project, env, 'Stop')
    assert blocked['decision'] == 'block'
    assert 'Missing code guard package(s): eslint.' in blocked['reason'], blocked
    repeated = invoke(engine, project, env, 'Stop', stop_hook_active=True)
    assert repeated['continue'] is False and 'decision' not in repeated, repeated
    verify = [*closeout, 'verify', '--review', 'Confirm the updated program prints 2.',
              '--', sys.executable, '-c',
              "import subprocess; assert subprocess.check_output(['node', 'app.js']) == b'2\\n'"]
    failed = run(verify, project, env)
    assert failed.returncode != 0 and 'Missing code guard package(s): eslint.' in failed.stderr
    install_from_diagnostic(failed.stderr, engine, project, env)
    success(run(verify, project, env))
    assert invoke(engine, project, env, 'Stop') == {}
    assert (project / 'app.js').read_text() == 'console.log(2);\n'


def portable_flow(engine, project, env):
    missing_node = dict(env, CODEX_GUARD_NODE=str(engine / 'missing-node'))
    output = invoke(engine, project, missing_node, 'PreToolUse')
    context = output['hookSpecificOutput']['additionalContext']
    assert 'Node.js is missing' in context and 'CODEX_GUARD_NODE' in context
    assert invoke(engine, project, env, 'PreToolUse') == {}
    (project / 'app.js').write_text('console.log(2);\n')
    shutil.rmtree(engine / 'node_modules/eslint')
    blocked = invoke(engine, project, env, 'Stop')
    assert blocked['decision'] == 'block'
    assert 'Missing code guard package(s): eslint.' in blocked['reason'], blocked
    repeated = invoke(engine, project, env, 'Stop', stop_hook_active=True)
    assert repeated['continue'] is False and 'decision' not in repeated, repeated
    install_from_diagnostic(repeated['systemMessage'], engine, project, env)
    assert invoke(engine, project, env, 'Stop', turn_id='resumed') == {}
    assert success(run([NODE, 'app.js'], project, env)) == '2\n'


def main():
    assert NODE
    with TemporaryDirectory(prefix='guard recovery ') as directory:
        root = Path(directory)
        for variant, exercise in [('local', local_flow), ('portable', portable_flow)]:
            home = root / variant
            engine = home / 'code-guard'
            project = home / 'project'
            project.mkdir(parents=True)
            (project / 'app.js').write_text('console.log(1);\n')
            env = dict(os.environ, CODEX_SESSION_ID='dependency-recovery-' + variant,
                       CODEX_GUARD_NODE=NODE, CODEX_HOME=str(home))
            env.pop('CODEX_GUARD_MD_HOME', None)
            if variant == 'local':
                local_engine(engine)
            else:
                with zipfile.ZipFile(ARCHIVE) as package:
                    for name in package.namelist():
                        if name.startswith('implementation/'):
                            target = home / name.removeprefix('implementation/')
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(package.read(name))
            success(run(['npm', 'ci', '--omit=optional', '--ignore-scripts', '--no-audit', '--no-fund'],
                        engine, env))
            exercise(engine, project, env)
            print(f'{variant}: missing dependency -> install -> same session verified', flush=True)


if __name__ == '__main__':
    main()
