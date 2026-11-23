"""Host payload shapes and hook-registration entries for the shared guard."""
import shlex
from pathlib import Path


EVENTS = {'codex': ('Stop',), 'claude': ('SessionStart', 'Stop'),
          'gemini': ('SessionStart', 'AfterAgent')}


def registrations(host, guard, python, node):
    command = shlex.join([str(python), str(guard), '--node', str(node), 'hook', host])
    result = {}
    for event in EVENTS[host]:
        hook = {'type': 'command', 'command': command}
        if host == 'gemini':
            hook['timeout'] = 180000
            hook['name'] = 'agent-toolkit-' + event.lower()
        else:
            hook['timeout'] = 180
        result[event] = {'hooks': [hook]}
    return result


def launch_context(event, guard, python, node):
    session = event.get('session_id')
    if not isinstance(session, str) or not session.strip():
        raise ValueError('The host did not supply a session ID.')
    command = shlex.join([str(python), str(guard), '--node', str(node), '--session', session])
    instructions = (f'Agent Toolkit is configured for this session. Before editing, run `{command} begin` '
                    f'in each worktree. After editing, run `{command} verify --review NOTE -- CHECK` '
                    'with the project verification command. Read-only tasks need no registration.')
    return {'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': instructions}}


def normalized_stop(host, event):
    session = event.get('session_id')
    if not isinstance(session, str) or not session.strip():
        raise ValueError('The host did not supply a session ID.')
    context = {'session_id': session, 'cwd': event.get('cwd'),
               'stop_hook_active': event.get('stop_hook_active') is True,
               'hook_event_name': 'Stop'}
    if host == 'codex':
        context['transcript_path'] = event.get('transcript_path')
    return context


def host_result(host, result):
    if host != 'gemini':
        return result
    if result.get('decision') == 'block':
        return {'decision': 'deny', 'reason': result['reason']}
    if result.get('continue') is False:
        return {'continue': False, 'reason': result['stopReason'],
                'systemMessage': result.get('systemMessage', result['stopReason'])}
    return result


def settings_path(host, codex_home, user_home):
    if host == 'codex':
        return Path(codex_home) / 'hooks.json'
    return Path(user_home) / ('.claude' if host == 'claude' else '.gemini') / 'settings.json'
