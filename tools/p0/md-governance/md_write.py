#!/usr/bin/python3
"""Analyze a staged candidate, then publish only after preflight and review."""
import argparse,fcntl,json,sys
from pathlib import Path
from md_gate import discover
from md_rules import lint,added_units,digest,similar_clauses
from md_store import verify_review,approve,atomic_text

def analyze(target,draft):
    old=target.read_text() if target.exists() else ''
    new=draft.read_text()
    candidates=[p for p in target.parents if (p/'.git').exists() or p.name=='.codex']
    root=candidates[0] if candidates else target.parent
    corpus={str(p):p.read_text() for p in discover(root)}
    return old,new,{'target':str(target.resolve()),'base_sha256':digest(old),'candidate_sha256':digest(new),'characters':len(new),'errors':lint(new,target,corpus),'new_clauses':added_units(old,new),'similar_clauses':similar_clauses(new,corpus,target)}
def apply(target,draft,review):
    if target.is_symlink(): raise ValueError('Refuse to replace a symlinked authority.')
    old,new,result=analyze(target,draft)
    if result['errors']: raise ValueError(json.dumps(result['errors'],ensure_ascii=False))
    verify_review(review,target,old,new)
    if (target.read_text() if target.exists() else '')!=old: raise ValueError('Original changed during preflight.')
    approve(target,old,new,review)
    atomic_text(target,new)
    return {'target':str(target),'sha256':digest(new),'status':'published after structural checks and independent review'}
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['analyze','review','apply'])
    parser.add_argument('--target',required=True); parser.add_argument('--draft',required=True)
    parser.add_argument('--review'); parser.add_argument('--request')
    parser.add_argument('--evidence',action='append',default=[])
    parser.add_argument('--retry', action='store_true', help='Retry an unavailable review after taking corrective action.')
    args=parser.parse_args(); target=Path(args.target).absolute(); draft=Path(args.draft)
    if args.action=='analyze': result=analyze(target,draft)[2]
    elif args.action=='review':
        from md_semantic import run
        if not args.request: raise ValueError('--request must name the concrete source/request text file.')
        result=run(target,draft,Path(args.request),args.evidence,args.retry)
    else:
        from md_store import STATE
        if not args.review: raise ValueError('--review must name an independent reviewer credential.')
        STATE.mkdir(mode=0o700,exist_ok=True)
        with (STATE/'publish.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            result=apply(target,draft,json.loads(Path(args.review).read_text()))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result.get('decision') in ('revise','unavailable'): sys.exit(2)
if __name__=='__main__':
    try: main()
    except Exception as e: print(str(e),file=sys.stderr); sys.exit(1)
