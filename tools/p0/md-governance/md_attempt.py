"""Keep one in-flight review per input and reuse unresolved feedback."""
from contextlib import contextmanager
import fcntl
import json

import md_store
import md_seal
from md_progress import ReviewFailure, progress


@contextmanager
def attempt_slot(key):
    directory = md_store.STATE / 'review-attempts'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = directory / (key + '.lock')
    with lock.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
        else:
            yield directory / (key + '.json')


def previous_feedback(slot, retry):
    if not slot.is_file():
        return None
    run_id = json.loads(slot.read_text())['run_id']
    destination = md_seal.run_directory(run_id)
    feedback = destination / 'feedback.json'
    if not feedback.is_file():
        return None
    result = json.loads(feedback.read_text())
    if result['decision'] == 'revise' or (result['decision'] == 'unavailable' and not retry):
        progress(f'Reusing existing review feedback. Diagnostics: {feedback}')
        return dict(result, reused=True, diagnostics=str(feedback))
    return None


def invoke_with_retry(invoke, packet, destination):
    try:
        return invoke(packet, destination)
    except ReviewFailure as error:
        if error.reason not in ('transport', 'connection_refused'):
            raise
        progress(f'Reviewer connection failed. Retrying once. First attempt: {destination}')
        return invoke(packet, destination / 'retry-1')
