"""Versioned inputs for one narrowly scoped independent instruction review."""
from pathlib import Path
from md_rules import digest, added_units, lint, similar_clauses
from md_gate import discover,linked_documents
import md_store


def root_for(target):
    candidates=[p for p in target.parents if (p/'.git').exists() or p.name=='.codex']
    return candidates[0] if candidates else target.parent


def document(identity,path,text=None):
    path=Path(path).resolve()
    content=path.read_text() if text is None else text
    return {'id':identity,'path':str(path),'exists':path.exists(),'sha256':digest(content),'text':content}


def linked_authorities(target, root, old, new):
    pending = linked_documents(target, root, old) + linked_documents(target, root, new)
    linked = {target}
    while pending:
        path = pending.pop().resolve()
        if path not in linked:
            linked.add(path)
            pending.extend(linked_documents(path, root))
    return linked - {target}


def governing_authorities(paths, target, root):
    return {path for path in paths if path.name.casefold() == 'agents.md'
            and (target.is_relative_to(path.parent) or not path.is_relative_to(root))}


def build(target,draft,request,evidence=()):
    if Path(target).is_symlink(): raise ValueError('Authority must not be a symlink.')
    target=Path(target).resolve(); draft=Path(draft).resolve()
    if target==draft: raise ValueError('Candidate must be a separate file and authority must not be a symlink.')
    old=target.read_text() if target.exists() else ''
    approved=md_store.read_json(md_store.STATE/'managed.json',{}).get(str(target))
    if approved and digest(old)!=approved:
        raise ValueError('Authority differs from its approved version; preserve current work and restore the approved base before proposing a candidate.')
    new=draft.read_text()
    root=root_for(target)
    linked = linked_authorities(target, root, old, new)
    paths = sorted(({p.resolve() for p in discover(root)} | linked) - {target})
    corpus = {str(path): path.read_text() for path in paths}
    similar = similar_clauses(new, corpus, target)
    related = {Path(match['source']) for row in similar for match in row['matches']}
    selected = sorted(linked | related | governing_authorities(paths, target, root))
    docs=[document('base',target,old),document('candidate',draft,new),document('request',request)]
    if not docs[-1]['text'].strip(): raise ValueError('A concrete request/source is required for independent review.')
    docs.extend(document('authority-'+str(i), path, corpus[str(path)]) for i, path in enumerate(selected))
    docs.extend(document('evidence-'+str(i),p) for i,p in enumerate(evidence))
    packet={'target':str(target),'draft':str(draft),'request':str(Path(request).resolve()),
            'evidence':[str(Path(p).resolve()) for p in evidence], 'documents':docs,
            'authority_manifest': {path: digest(text) for path, text in corpus.items()},
            'added':added_units(old,new),'removed':added_units(new,old),
            'structural_errors':lint(new,target,corpus),'similar_clauses':similar}
    return old,new,packet
