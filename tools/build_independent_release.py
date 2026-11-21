"""Build other-user reference sources; never include live state or credentials."""
import ast
import os
import re
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
LIVE=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
BASE=ROOT/'releases/2026-11-21-integrated/Codex-优化整合源码包.zip'
DEST=ROOT/'releases/2026-11-21-independent-review'


def portable_hook(text):
    text=text.replace('import fcntl,','import fcntl,shutil,',1)
    text=re.sub(r'^NODE=.*$',"NODE=os.environ.get('CODEX_GUARD_NODE') or shutil.which('node')",text,count=1,flags=re.M)
    text=text.replace('def run_scan(root,previous,delegated_roots=()):', '''def run_scan(root,previous,delegated_roots=()):
    if not NODE: raise RuntimeError('Node executable missing; configure CODEX_GUARD_NODE.')
    env=dict(os.environ)
    env.setdefault('CODEX_HOME',str(BASE.parent))
    env.setdefault('CODEX_GUARD_PYTHON',sys.executable)
    env.setdefault('CODEX_GUARD_MD_HOME',str(BASE.parent/'md-governance'))''')
    return text.replace('capture_output=True,timeout=90)', 'capture_output=True,timeout=90,env=env)',1)


def portable_markdown(text):
    home=str(LIVE)
    text=text.replace('[审查策略]('+home+'/md-governance/review_policy.json)', '`$CODEX_HOME/md-governance/review_policy.json` 中的审查策略')
    text=text.replace('('+home+'/skills/instruction-maintenance/SKILL.md)', '(../instruction-maintenance/SKILL.md)')
    text=text.replace(home,'$CODEX_HOME')
    return text


def collect():
    with zipfile.ZipFile(BASE) as source:
        entries={name:source.read(name) for name in source.namelist() if name!='PACKAGE-MANIFEST.json'}
    for p in sorted((LIVE/'md-governance').glob('*.py')):
        text=p.read_text()
        if p.name=='md_gate.py':
            text=re.sub(r'^GLOBAL=.*$',"GLOBAL=Path(os.environ.get('CODEX_HOME',str(Path(__file__).resolve().parent.parent)))/'AGENTS.md'",text,count=1,flags=re.M)
        entries['implementation/md-governance/'+p.name]=text.encode()
    entries['implementation/md-governance/review_policy.json']=(LIVE/'md-governance/review_policy.json').read_bytes()
    entries['implementation/code-guard/hook.py']=portable_hook((LIVE/'code-guard/hook.py').read_text()).encode()
    assignment=next(line for line in (LIVE/'code-guard/hook.py').read_text().splitlines() if line.startswith('NODE='))
    try: node=ast.literal_eval(assignment.split('=',1)[1])
    except (ValueError,SyntaxError): node=None
    for p in (LIVE/'code-guard').glob('test*.py'):
        text=p.read_text()
        if node: text=text.replace(repr(node),"__import__('shutil').which('node')")
        entries['implementation/code-guard/'+p.name]=text.encode()
    for name in ['local-code-guard','instruction-maintenance']:
        text=(LIVE/'skills'/name/'SKILL.md').read_text()
        entries['implementation/skills/'+name+'/SKILL.md']=portable_markdown(text).encode()
    acceptance=LIVE/'md-governance/audit/20261121-independent/native-acceptance.json'
    entries['validation/independent-review/native-acceptance.json']=acceptance.read_bytes()
    guide=ROOT/'work/md-independent/guide.stage'
    entries['Codex-异机迁移实施指南.md']=guide.read_bytes()
    return entries


def build():
    entries=collect()
    for name,data in entries.items():
        if name.endswith(('.py','.md','.json','.mjs','.txt')):
            assert b'/Users/a0000' not in data,name
            assert b'/tmp/md-independent' not in data,name
    manifest={'version':'2026.11.21-independent-review','purpose':'Other-user reference implementation; not an auto-installer or proof of recipient activation',
              'validation':{'md_tests':43,'code_tests':66,'native_policy_cases':6,'scope':'same-host isolated and relocated reference source; not proof of recipient activation'},
              'private_state_included':False,'recipient_setup':'Resolve CODEX_HOME before installation; if unset, use ~/.codex. Keep recipient permissions.','independent_review':'Existing native Codex CLI login; no new API key or author-signed approval fallback',
              'sha256':{name:hashlib.sha256(data).hexdigest() for name,data in sorted(entries.items())}}
    entries['PACKAGE-MANIFEST.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    DEST.mkdir(parents=True,exist_ok=True)
    output=DEST/'Codex-独立审查升级源码包.zip'
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in sorted(entries.items()):archive.writestr(name,data)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for name,expected in manifest['sha256'].items():assert hashlib.sha256(archive.read(name)).hexdigest()==expected
    (DEST/'交付清单.json').write_text(json.dumps(dict(manifest,archive=output.name,archive_sha256=hashlib.sha256(output.read_bytes()).hexdigest()),ensure_ascii=False,indent=2)+'\n')
    print(output)
    return output


if __name__=='__main__':
    build()
