"""Executable claims: real hooks and real files, independent behavior oracles."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'paired-20261121'))
from runner import prepare_engine, LIVE

HERE = Path(__file__).resolve().parent
RESULTS = []


class Fixture:
    def __init__(self, root, name):
        self.home = root/name/'agent-home'
        self.home.mkdir(parents=True)
        prepare_engine(self.home)
        self.project = root/name/'project'
        self.project.mkdir()
        (self.project/'app.js').write_text('module.exports={run:()=>0};\n')
        self.event = {'cwd': str(self.project), 'session_id': name, 'turn_id': name}
        self.events = []
        self.invoke('PreToolUse')
        self.invoke('PostToolUse')
    def invoke(self, kind, **fields):
        self.event.update(fields, hook_event_name=kind)
        result = subprocess.run(['/usr/bin/python3', str(self.home/'code-guard/hook.py')], input=json.dumps(self.event), text=True, capture_output=True, check=True, timeout=30)
        outcome = json.loads(result.stdout)
        self.events.append({'event': dict(self.event), 'outcome': outcome})
        return outcome
    def edit(self, content, name='app.js'):
        self.invoke('PreToolUse')
        (self.project/name).write_text(content)
        return self.invoke('PostToolUse')
    def expect(self, expected):
        p = subprocess.run(['node', '-e', "console.log(JSON.stringify(require('./app').run()))"], cwd=self.project, text=True, capture_output=True, check=True)
        assert json.loads(p.stdout) == expected, p.stdout


def record(name, fixture, **details):
    RESULTS.append({'effect': name, 'passed': True, **details, 'events': fixture.events})


def source_case(root, name, bad, good, needle, expected):
    fixture = Fixture(root, name)
    feedback = fixture.edit(bad)
    assert needle in str(feedback), feedback
    fixture.expect(expected)
    rejected = fixture.invoke('Stop')
    assert rejected.get('decision') == 'block', rejected
    fixture.edit(good)
    fixture.expect(expected)
    accepted = fixture.invoke('Stop')
    assert accepted == {}, accepted
    record(name, fixture, rejected_source=bad, repaired_source=good, independently_expected=expected,
           outcome='Immediate feedback, final rejection, then repaired behavior and clear final gate')


def long_function(root):
    bad = 'function run(){\nlet n=0;\n'+'n+=1;\n'*81+'return n;\n}\nmodule.exports={run};\n'
    good = ''.join('function part%d(){\nlet n=0;\n'%i+'n+=1;\n'*27+'return n;\n}\n' for i in range(3))
    good += 'function run(){return part0()+part1()+part2();}\nmodule.exports={run};\n'
    source_case(root, 'long-function', bad, good, 'too many lines', 81)


def complexity(root):
    bad = 'function choose(x){\n'+''.join(f'if(x==={i})return {i};\n' for i in range(16))+'return -1;\n}\nmodule.exports={run:()=>choose(7)};\n'
    good = 'function choose(x){return Number.isInteger(x)&&x>=0&&x<16?x:-1;}\nmodule.exports={run:()=>choose(7)};\n'
    source_case(root, 'complexity', bad, good, 'complexity', 7)


def deprecated(root):
    bad = "const sys=require('sys');\nconst fs=require('node:fs');\nfunction exists(p){return new Promise(r=>fs.exists(p,r));}\nfunction run(){return [new Buffer('你好','utf8').toString('base64'),sys.isArray([])];}\nmodule.exports={run,exists};\n"
    good = "const fs=require('node:fs');\nfunction exists(p){return Promise.resolve(fs.existsSync(p));}\nfunction run(){return [Buffer.from('你好','utf8').toString('base64'),Array.isArray([])];}\nmodule.exports={run,exists};\n"
    source_case(root, 'deprecated-interfaces', bad, good, 'Deprecated', ['5L2g5aW9', True])


def reviewed_instruction(root):
    fixture = Fixture(root, 'review-required')
    target = fixture.project/'AGENTS.md'
    old = '# Project\n\nPublishing requires explicit user authorization.\n'
    fixture.edit(old, 'AGENTS.md')
    assert 'version-bound' in str(fixture.invoke('Stop'))
    fixture.invoke('PreToolUse')
    target.unlink()
    fixture.invoke('PostToolUse')
    draft = fixture.project/'instructions.stage'
    draft.write_text(old)
    writer = fixture.home/'md-governance/md_write.py'
    args = ['/usr/bin/python3', str(writer), 'analyze', '--target', str(target), '--draft', str(draft)]
    analysis = json.loads(subprocess.check_output(args, text=True))
    assert not analysis['errors']
    review = {key: analysis[key] for key in ['target', 'base_sha256', 'candidate_sha256']}
    review.update(basis='This fixture preserves the explicit user authorization requirement.', ownership='The project AGENTS owns the publication authorization boundary.', overlap='The fixture has one publication clause and no other active owner.', conflicts='No automatic publishing permission is introduced by this unchanged candidate.', preservation='The candidate retains the complete original authorization condition.', clauses=[])
    for clause in analysis['new_clauses']:
        assert clause['text']=='Publishing requires explicit user authorization.', clause
        review['clauses'].append({'id':clause['id'],'source':'User requested proof of the publication authorization review boundary.','reason':'This single publication condition retains explicit user authorization and introduces no automatic publishing exception.'})
    review_path = fixture.project/'review.json'
    review_path.write_text(json.dumps(review))
    fixture.invoke('PreToolUse')
    subprocess.run(args[:2]+['apply']+args[3:]+['--review', str(review_path)], text=True, capture_output=True, check=True)
    fixture.invoke('PostToolUse')
    assert fixture.invoke('Stop') == {}
    record('unreviewed-instruction-write', fixture, analysis=analysis, review=review,
           outcome='Direct write rejected; exact same content accepted only after valid preflight and version-bound review')


def draft_rejections(root):
    fixture = Fixture(root, 'draft-checks')
    target = fixture.project/'AGENTS.md'
    target.write_text('# Project\n\nPublishing requires explicit user authorization.\n')
    original = target.read_bytes()
    writer = fixture.home/'md-governance/md_write.py'
    findings = []
    for name, content, rule in [('same-paragraph-duplicate', '# Project\n\nKeep this task scoped.\nKeep this task scoped.\n', 'md:duplicate'),
                                ('separate-paragraph-duplicate', '# Project\n\nKeep this task scoped.\n\nKeep this task scoped.\n', 'md:duplicate'),
                                ('oversize', '# Project\n\n'+'x'*3100+'\n', 'md:size')]:
        draft = fixture.project/(name+'.stage')
        draft.write_text(content)
        args = ['/usr/bin/python3', str(writer), 'analyze', '--target', str(target), '--draft', str(draft)]
        analysis = json.loads(subprocess.check_output(args, text=True))
        detected=any(key.startswith(rule) for key in analysis['errors'])
        if not detected:
            assert name=='same-paragraph-duplicate', analysis
            findings.append({'case':name,'expected_duplicate_detection':True,'actually_detected':False,'errors':analysis['errors'],'authority_unchanged':target.read_bytes()==original})
            continue
        review = fixture.project/'empty-review.json'
        review.write_text('{}')
        result = subprocess.run(args[:2]+['apply']+args[3:]+['--review', str(review)], capture_output=True, text=True)
        assert result.returncode != 0 and target.read_bytes() == original
        findings.append({'case': name, 'errors': analysis['errors'], 'apply_exit': result.returncode, 'authority_unchanged': True,'actually_detected':True})
    record('duplicate-and-size-checks', fixture, passed=all(row['actually_detected'] for row in findings), checks=findings, limitation='Same-paragraph repeated sentences are not detected by the structural duplicate checker; semantic duplication is not guaranteed.')


def ownership(root):
    fixture = Fixture(root, 'root-ownership')
    other = root/'root-ownership/other-project'
    other.mkdir()
    (other/'app.js').write_text('const safe = 1;\n')
    fixture.invoke('PreToolUse', cwd=str(other))
    fixture.invoke('PostToolUse')
    fixture.invoke('PreToolUse', session_id='foreign-chat', cwd=str(fixture.project))
    (fixture.project/'foreign.js').write_text('new Buffer(1);\n')
    fixture.invoke('PostToolUse')
    outside = fixture.invoke('Stop', session_id='root-ownership', cwd=str(other))
    assert outside.get('decision') != 'block', outside
    assert 'not certification' in str(outside), outside
    fixture.invoke('PreToolUse', cwd=str(other))
    (other/'own.js').write_text('new Buffer(2);\n')
    immediate = fixture.invoke('PostToolUse')
    assert 'Deprecated' in str(immediate), immediate
    own = fixture.invoke('Stop', turn_id='own-violation')
    assert own.get('decision') == 'block', own
    record('foreign-warning-own-violation-blocked', fixture, outcome='Foreign chat debt warned; this task own write immediately rejected and blocked at finalization')


def empty_coverage(root):
    fixture = Fixture(root, 'empty-coverage')
    (fixture.project/'app.js').unlink()
    outcome = fixture.invoke('Stop')
    assert 'verification not performed' in str(outcome), outcome
    record('empty-scan-not-certified', fixture, outcome='No source files explicitly reported as unverified, not a passing code check')


def main():
    sources = [LIVE/'code-guard'/name for name in ['hook.py', 'scan.mjs', 'policies.json', 'python_metrics.py']]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    with tempfile.TemporaryDirectory(prefix='effect-proofs-') as directory:
        root = Path(directory)
        for test in [long_function, complexity, deprecated, reviewed_instruction, draft_rejections, ownership, empty_coverage]:
            test(root)
    assert hashes == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}, 'Installed checker changed during proofs'
    report = {'source_sha256': hashes, 'effects': RESULTS, 'effects_passed': sum(row['passed'] for row in RESULTS),
              'not_proven': ['General code age detection', 'Automatic elimination of all semantic duplicates', 'Business correctness from lint alone', 'General improvement in agent intelligence, speed or task success']}
    (HERE/'proofs.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'effects_passed': sum(row['passed'] for row in RESULTS), 'source_version': hashes[str(LIVE/'code-guard/hook.py')]}))


if __name__ == '__main__':
    main()
