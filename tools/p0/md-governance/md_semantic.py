"""Review, actionable feedback and version-bound approval for publication."""
from collections import Counter
import json
import uuid
from pathlib import Path
import md_backend
from md_attempt import attempt_slot, invoke_with_retry, previous_feedback
import md_context
import md_seal
from md_rules import digest
from md_store import atomic_text
from md_verdict import validate,POLICY
from md_progress import ReviewFailure, progress


def fingerprint(packet):
    return digest(md_seal.canonical(packet))


def check_verdict(verdict, packet):
    try:
        return validate(verdict, packet)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        clauses = verdict.get('clauses') if isinstance(verdict, dict) else []
        items = clauses if isinstance(clauses, list) else []
        seen = Counter(item['id'] for item in items if isinstance(item, dict) and isinstance(item.get('id'), str))
        expected = {item['id']: item['line'] for item in packet['added']}
        missing = [str(line) for key, line in expected.items() if key not in seen]
        details = [str(error)]
        if missing:
            details.append(f'Missing judgments for candidate lines {", ".join(missing)}.')
        if len(seen.keys() - expected.keys()):
            details.append(f'Unexpected clause IDs: {len(seen.keys() - expected.keys())}.')
        if any(count > 1 for count in seen.values()):
            details.append('The response contains repeated clause IDs.')
        raise ReviewFailure('invalid_response', ' '.join(details)) from error


def quality_versions():
    return {p.name:digest(p.read_text()) for p in sorted(Path(__file__).parent.glob('md_*.py'))}


def attempt_inputs(packet, versions, policy_version):
    documents = [(document['id'], document['sha256'], document['exists'],
                  None if document['id'] == 'candidate' else document['path'])
                 for document in packet['documents']]
    return [packet['target'], documents, packet['authority_manifest'], versions, policy_version]


def run(target,draft,request,evidence=(),retry=False):
    progress(f'Preparing independent review for {target}.')
    old,new,packet=md_context.build(target,draft,request,evidence)
    if packet['structural_errors']:
        return {'decision':'revise','stage':'structure','errors':packet['structural_errors'],'published':False,
                'next_action':'Merge the identified repeats, correct links, or shorten/move content within existing budgets; rerun analyze.'}
    if old==new: raise ValueError('No candidate change; cannot mint independent approval for an already written instruction.')
    versions=quality_versions(); policy_version=digest(POLICY.read_text())
    attempt_key = fingerprint(attempt_inputs(packet, versions, policy_version))
    with attempt_slot(attempt_key) as slot:
        if slot is None:
            return {'decision': 'unavailable', 'reason': 'review_in_progress', 'published': False,
                    'next_action': 'The same input is already under review. Use the result of that run.'}
        prior = previous_feedback(slot, retry)
        if prior is not None:
            return prior
        feedback = review_packet(target,draft,request,evidence,packet,versions,policy_version)
        atomic_text(slot, json.dumps({'run_id': feedback['run_id']}))
        return feedback


def review_packet(target,draft,request,evidence,packet,versions,policy_version):
    run_id=uuid.uuid4().hex
    destination=md_seal.run_directory(run_id)
    destination.mkdir(mode=0o700,parents=True)
    atomic_text(destination/'input.json',json.dumps(packet,ensure_ascii=False,indent=2)+'\n')
    try:
        verdict,execution=invoke_with_retry(md_backend.invoke,packet,destination)
        if versions!=quality_versions() or policy_version!=digest(POLICY.read_text()):
            raise ValueError('Quality checker or criteria changed during review; rerun against current versions.')
        accepted=check_verdict(verdict,packet)
        _,_,current=md_context.build(target,draft,request,evidence)
        if fingerprint(current)!=fingerprint(packet): raise ValueError('Review inputs changed while reviewer was running; retry against current versions.')
        payload={'run_id':run_id,'input_sha256':fingerprint(packet),'packet':packet,
                 'policy_sha256':policy_version,'quality_sha256':versions,
                 'verdict':verdict,'execution':execution}
        atomic_text(destination/'attestation.json',json.dumps(md_seal.signed(payload),ensure_ascii=False,indent=2)+'\n')
        token={'independent_review':run_id}
        atomic_text(destination/'review.json',json.dumps(token)+'\n')
        feedback={'decision':'pass' if accepted else 'revise','published':False,'review':str(destination/'review.json'),
                  'run_id':run_id,'verdict':verdict,'execution':execution,
                  'next_action': 'Apply the reviewed draft.' if accepted else 'Address the cited feedback, then review the updated draft.'}
    except Exception as error:
        reason = error.reason if isinstance(error,ReviewFailure) else 'review_unavailable'
        feedback={'decision':'unavailable','reason':reason,'published':False,'run_id':run_id,'error':str(error),
                  'next_action':'Keep the draft. Resolve the diagnostic, then rerun review with --retry if the inputs are unchanged.'}
    atomic_text(destination/'feedback.json',json.dumps(feedback,ensure_ascii=False,indent=2)+'\n')
    progress(f"Independent review result: {feedback['decision']}. Diagnostics: {destination / 'feedback.json'}")
    return feedback


def require(review,target,old,new):
    run_id=review.get('independent_review','')
    if not run_id: raise ValueError('A separate independent review is required; author-authored review JSON is not approval.')
    directory=md_seal.run_directory(run_id)
    path=directory/'attestation.json'
    if not path.exists(): raise ValueError('Missing independent reviewer attestation.')
    record=md_seal.verified(json.loads(path.read_text()))
    if record['run_id']!=run_id: raise ValueError('Independent review run mismatch.')
    packet=record['packet']
    if packet['target']!=str(Path(target).resolve()): raise ValueError('Independent review target mismatch.')
    if packet['documents'][0]['sha256']!=digest(old) or packet['documents'][1]['sha256']!=digest(new):
        raise ValueError('Stale independent review: original or candidate changed.')
    _,_,current=md_context.build(target,Path(packet['draft']),Path(packet['request']),packet['evidence'])
    if fingerprint(current)!=record['input_sha256'] or digest(POLICY.read_text())!=record['policy_sha256']:
        raise ValueError('Stale independent review: context, evidence or quality criteria changed.')
    if record.get('quality_sha256')!=quality_versions():
        raise ValueError('Stale independent review: quality checker implementation changed.')
    if not validate(record['verdict'],packet): raise ValueError('Independent review requires revision; read feedback.json.')
    return record


def verdict_passed(verdict,packet):
    judgments=list(verdict['dimensions'].values())+verdict['clauses']
    covered={item['id'] for item in verdict['clauses']}=={item['id'] for item in packet['added']}
    return verdict['decision']=='pass' and bool(verdict['dimensions']) and covered and all(item['status']=='pass' for item in judgments)


def published(record):
    """A receipt must trace to a sealed passing run, not any signed JSON."""
    try:
        directory=md_seal.run_directory(record['run_id'])
        attestation=md_seal.verified(json.loads((directory/'attestation.json').read_text()))
        packet=attestation['packet']; execution=attestation['execution']
        bound=(record['input_sha256']==attestation['input_sha256'] and record['target']==packet['target'] and
               record['candidate_sha256']==packet['documents'][1]['sha256'])
        native=execution.get('provider')=='native Codex CLI' and execution.get('returncode')==0 and execution.get('tools_used') is False
        return bound and native and verdict_passed(attestation['verdict'],packet)
    except (ValueError,KeyError,OSError,TypeError): return False
