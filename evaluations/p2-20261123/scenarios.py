"""Fixed task proposals and independent oracles for the P1/P2 workflow replay."""
from dataclasses import dataclass
import json
import sys


@dataclass(frozen=True)
class Scenario:
    name: str
    request: str
    behavior: str
    expected: str
    proposal: str
    documented: str
    repair: str = ''
    counterexample: tuple = ()
    weak: bool = False
    adjacent: bool = False


PARCEL = "items = [3, 2]\nprint(f'Fee units: {sum(items)}')\n"
RETRY = '''remaining = 9
seen = set()
for request in ('Q1', 'Q1'):
    if request not in seen:
        remaining -= 2
    seen.add(request)
print(f'Stock available: {remaining}')
'''
PERMISSION = '''actor = 'staff-A'
owner = 'staff-B'
print('Visible: yes' if actor == owner else 'Visible: no')
'''


CASES = (
    Scenario('stale-doc', 'Publish the fee total for a parcel with charges 3 and 2.',
             'Parcel fees are the sum of line charges.', 'Fee units: 5', PARCEL, 'Fee units: 4'),
    Scenario('weak-retry-test', 'A repeated dispatch confirmation may deduct two units only once from nine.',
             'A confirmation ID changes stock only once.', 'Stock available: 7', RETRY,
             'Stock available: 7', counterexample=('if request not in seen:', 'if True:', 'Stock available: 5'),
             weak=True),
    Scenario('incorrect-refund', 'Refund 91 cents less a fixed 23-cent fee.',
             'Refund subtracts the fixed fee from the original charge.', 'Refund cents: 68',
             "paid, fee = 91, 24\nprint(f'Refund cents: {paid - fee}')\n", 'Refund cents: 68',
             repair="paid, fee = 91, 23\nprint(f'Refund cents: {paid - fee}')\n"),
    Scenario('correct-permission-change', 'Staff A must not see staff B’s item.',
             'Only an item owner can see that item.', 'Visible: no', PERMISSION, 'Visible: no',
             counterexample=('actor == owner', 'bool(actor)', 'Visible: yes')),
    Scenario('unrelated-copy', 'Clarify a changelog sentence without changing parcel fees.',
             'Parcel fees are the sum of line charges.', 'Fee units: 5', PARCEL, 'Fee units: 5',
             adjacent=True),
)


SMOKE = '''import subprocess
import sys

value = subprocess.check_output([sys.executable, 'app.py'], text=True)
assert value.strip()
'''


def document(case, output, note=''):
    return f'# Behavior\n\n{case.behavior}\n\n```text\n{output}\n```\n{note}'


def test_source(expected, weak=False):
    predicate = 'bool(observed)' if weak else f'observed == {expected!r}'
    return f'''{SMOKE}
observed = value.rstrip('\\n')
if not ({predicate}):
    print('BUSINESS_INVARIANT_FAILED', file=sys.stderr)
    sys.exit(1)
print('BUSINESS_INVARIANT_CHECKED')
'''


def acceptance(case):
    scenario = {'id': case.name, 'inputs': ['app.py'], 'document': 'README.md', 'behavior': case.behavior,
                'example': {'argv': [sys.executable, 'app.py'], 'output': case.expected}}
    if case.counterexample:
        before, after, _ = case.counterexample
        scenario['inputs'].append('test_case.py')
        scenario['test'] = {'argv': [sys.executable, 'test_case.py'], 'success': 'BUSINESS_INVARIANT_CHECKED'}
        scenario['counterexample'] = {'path': 'app.py', 'before': before, 'after': after,
                                      'failure': 'BUSINESS_INVARIANT_FAILED'}
    return json.dumps({'version': 1, 'scenarios': [scenario]}, ensure_ascii=False, indent=2) + '\n'


def baseline(case):
    app = case.proposal if case.adjacent else "print('Previous behavior')\n"
    output = case.expected if case.adjacent else 'Previous behavior'
    files = {'app.py': app, 'README.md': document(case, output), 'smoke.py': SMOKE,
             '.agent-toolkit/acceptance.json': acceptance(case)}
    if case.counterexample:
        files['test_case.py'] = test_source(output)
    return files


def proposal(case):
    if case.adjacent:
        return {'changelog.md': 'Clarify the changelog without changing tracked behavior.\n'}
    files = {'app.py': case.proposal, 'README.md': document(case, case.documented)}
    if case.counterexample:
        files['test_case.py'] = test_source(case.expected, weak=case.weak)
    return files


def correction(case):
    files = {'README.md': document(case, case.expected, '\nRechecked after the correction.\n')}
    if case.repair:
        files['app.py'] = case.repair
    if case.weak:
        files['test_case.py'] = test_source(case.expected)
    return files
