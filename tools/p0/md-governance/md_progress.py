"""Bound reviewer calls and display progress without echoing review inputs."""
import os
import signal
import subprocess
import sys
import time


DEADLINE = 360
HEARTBEAT = 15


class ReviewFailure(ValueError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def progress(message):
    print(message, file=sys.stderr, flush=True)


def finish_process(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return process.communicate()


def run_reviewer(command, prompt, env, destination, timeout=DEADLINE, interval=HEARTBEAT):
    started = time.monotonic()
    progress(f'Independent review started. Input: {len(prompt)} characters. Diagnostics: {destination}')
    with subprocess.Popen(command, env=env, text=True, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          start_new_session=True) as process:
        incoming = prompt
        try:
            while True:
                elapsed = time.monotonic() - started
                if elapsed >= timeout:
                    stdout, stderr = finish_process(process)
                    raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
                try:
                    stdout, stderr = process.communicate(incoming, timeout=min(interval, timeout - elapsed))
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    incoming = None
                    elapsed = time.monotonic() - started
                    progress(f'Independent reviewer still running after {elapsed:.0f}s. Diagnostics: {destination}')
        except BaseException:
            if process.poll() is None:
                finish_process(process)
            raise
