"""Exercise the Stop-only protocol via the native Codex CLI and a loopback model."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest

from test_runtime import BASE, CODEX, FixtureServer


class StopServer(FixtureServer):
    def __init__(self, project, mode, command):
        self.entry = command
        super().__init__(project, mode)

    def next_items(self, payload):
        items = super().next_items(payload)
        if len(self.requests) == 1 and self.mode != 'clean':
            edit = "printf 'const value = 2;\\n' > app.js; cat read-marker.txt"
            command = f'{self.entry} begin && {edit}' if self.mode == 'repair' else edit
            item = items[0]
            args = json.loads(item['arguments'])
            item['arguments'] = json.dumps(dict(args, cmd=command))
        if len(self.requests) == 3 and self.mode == 'repair':
            verify = f"{self.entry} verify --review 'The configured value is 2.' -- node --check app.js"
            args = json.loads(items[0]['arguments'])
            command = "printf 'The configured value is 2.\\n' > README.md && " + verify
            items[0]['arguments'] = json.dumps(dict(args, cmd=command))
        return items


def run_git(project, *args):
    subprocess.run(['git', '-C', str(project), *args], check=True, capture_output=True, timeout=10)


def write_hook(home, command):
    wrapper = home / 'hook-wrapper.py'
    log = home / 'stop-events.jsonl'
    source = f'''import json, subprocess, sys
from pathlib import Path
event = json.load(sys.stdin)
reply = subprocess.run({command + ['stop']!r}, input=json.dumps(event),
                       text=True, capture_output=True, check=True)
with Path({str(log)!r}).open('a') as out:
    out.write(json.dumps({{'event': event, 'output': json.loads(reply.stdout)}}) + '\\n')
print(reply.stdout)
'''
    wrapper.write_text(source)
    hook = {'type': 'command', 'command': shlex.join([sys.executable, str(wrapper)]), 'timeout': 30}
    (home/'hooks.json').write_text(json.dumps({'hooks': {'Stop': [{'hooks': [hook]}]}}))
    return log


def runtime_config(port):
    return f'''model = "gpt-6.2"
model_provider = "fixture"
approval_policy = "never"
sandbox_mode = "danger-full-access"
[features]
hooks = true
shell_snapshot = false
enable_request_compression = false
[model_providers.fixture]
name = "Loopback fixture"
wire_api = "responses"
requires_openai_auth = false
base_url = "http://127.0.0.1:{port}/v1"
'''


@unittest.skipUnless(CODEX, 'native Codex CLI not installed')
class StopRuntimeTests(unittest.TestCase):
    def run_case(self, mode):
        with tempfile.TemporaryDirectory(prefix='toolkit-stop-runtime-') as directory:
            home = Path(directory) / 'home'
            home.mkdir()
            project = Path(directory) / 'project'
            project.mkdir()
            (project/'read-marker.txt').write_text('READ_RESULT_PRESERVED\n')
            (project/'app.js').write_text('const value = 1;\n')
            run_git(project, 'init', '-q')
            run_git(project, 'add', '.')
            run_git(project, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                    'commit', '-qm', 'Fixture baseline')
            command = [sys.executable, str(BASE/'closeout.py')]
            events = write_hook(home, command)
            server = StopServer(project, mode, shlex.join(command))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                self.run_cli(home, project, server)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)
            self.assertIsNone(server.failure, server.failure)
            replies = [json.loads(line) for line in events.read_text().splitlines()]
            self.assertTrue(all(row['event']['hook_event_name'] == 'Stop' for row in replies))
            return [row['output'] for row in replies]

    def run_cli(self, home, project, server):
        (home/'config.toml').write_text(runtime_config(server.server_port))
        env = dict(os.environ, CODEX_HOME=str(home))
        for name in ('OPENAI_API_KEY', 'CODEX_SESSION_ID', 'CODEX_THREAD_ID'):
            env.pop(name, None)
        process = subprocess.run([CODEX, '--no-daemon', 'exec', '--dangerously-bypass-hook-trust',
                                  '--json', '-C', str(project), 'Run the isolated loopback fixture.'],
                                 env=env, text=True, capture_output=True, timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr[-4000:])

    def test_read_only_does_not_block(self):
        self.assertEqual(self.run_case('clean'), [{}])

    def test_unregistered_write_is_visible(self):
        results = self.run_case('unregistered')
        self.assertEqual(len(results), 1, results)
        self.assertIn('without a pre-edit baseline', results[0].get('systemMessage', ''))

    def test_missing_then_current_evidence(self):
        results = self.run_case('repair')
        self.assertEqual(results[0].get('decision'), 'block', results)
        self.assertEqual(results[-1], {}, results)
