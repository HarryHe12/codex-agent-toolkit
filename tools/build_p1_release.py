"""Build the P1 installer and adapters from the verified P0 archive."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from build_p0_release import ARCHIVE_NAME, ROOT, RELEASE as P0, export, validate


SOURCE = ROOT / 'tools/p1'
VERSION = '2026.11.23-p1'
RELEASE = ROOT / 'releases/2026-11-23-p1'
MANIFEST = 'PACKAGE-MANIFEST.json'


def digest(content):
    return hashlib.sha256(content).hexdigest()


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode()


def replace(text, source, destination):
    if text.count(source) != 1:
        raise ValueError(f'P0 compatibility anchor changed: {source}')
    return text.replace(source, destination)


def scanner_overlay(entries):
    hook_name = 'implementation/code-guard/hook.py'
    code = entries[hook_name].decode()
    code = replace(code, 'def run_scan(root,previous,delegated_roots=()):',
                   'def run_scan(root,previous,delegated_roots=(),*,policy_root=None,source_only=False):')
    code = replace(code, "input=json.dumps({'root':str(root),'previous':previous,'delegatedRoots':list(delegated_roots)})",
                   "input=json.dumps({'root':str(root),'previous':previous,'delegatedRoots':list(delegated_roots),"
                   "'policyRoot':str(policy_root or root),'sourceOnly':source_only})")
    entries[hook_name] = code.encode()
    scanner_name = 'implementation/code-guard/scan.mjs'
    code = entries[scanner_name].decode()
    code = replace(code, 'path.resolve(item.root) === path.resolve(request.root)',
                   'path.resolve(item.root) === path.resolve(request.policyRoot || request.root)')
    code = replace(code, 'path.resolve(item.root,item.module)', 'path.resolve(request.root,item.module)')
    start = code.index("const {spawnSync: scanDocuments} = await import('node:child_process');")
    end = code.index('process.stdout.write(JSON.stringify(result));', start)
    documents = '\n'.join('  ' + line for line in code[start:end].splitlines())
    code = code[:start] + f'if (!request.sourceOnly) {{\n{documents}\n}}\n' + code[end:]
    entries[scanner_name] = code.encode()


def collect():
    base = P0 / ARCHIVE_NAME
    expected = json.loads((P0 / '交付清单.json').read_text())['archive_sha256']
    if digest(base.read_bytes()) != expected:
        raise ValueError('P0 archive does not match its committed checksum.')
    with zipfile.ZipFile(base) as archive:
        pinned = json.loads(archive.read(MANIFEST))['sha256']
        entries = {name: archive.read(name) for name in pinned}
    if any(digest(content) != pinned[name] for name, content in entries.items()):
        raise ValueError('P0 archive member differs from its manifest.')
    scanner_overlay(entries)
    for path in SOURCE.glob('*.py'):
        key = path.name if path.name == 'install.py' else 'implementation/toolkit/' + path.name
        entries[key] = path.read_bytes()
    entries.update({f'examples/{path.name}': path.read_bytes() for path in (SOURCE / 'examples').iterdir()
                    if path.is_file()})
    entries['guides/Codex-P1-安装与接入.md'] = (SOURCE / 'guide.md').read_bytes()
    entries['README.md'] = (SOURCE / 'public-readme.md').read_bytes()
    validate(entries)
    manifest = {'version': VERSION, 'base_archive_sha256': expected,
                'sha256': {name: digest(data) for name, data in sorted(entries.items())}}
    return dict(entries, **{MANIFEST: json_bytes(manifest)})


def build(entries, destination):
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / ARCHIVE_NAME
    with zipfile.ZipFile(path, 'w') as archive:
        for name, content in sorted(entries.items()):
            archive.writestr(zipfile.ZipInfo(name), content, compress_type=zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() or any(archive.read(name) != data for name, data in entries.items()):
            raise ValueError('Built P1 archive differs from its release sources.')
    receipt = dict(json.loads(entries[MANIFEST]), archive=ARCHIVE_NAME, archive_sha256=digest(path.read_bytes()))
    (destination / '交付清单.json').write_bytes(json_bytes(receipt))
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', type=Path)
    args = parser.parse_args()
    entries = collect()
    if args.export:
        export(entries, args.export)
    else:
        print(build(entries, RELEASE))


if __name__ == '__main__':
    main()
