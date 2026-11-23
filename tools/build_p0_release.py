"""Build identical portable archive and public repository sources from pinned inputs."""
import argparse
from pathlib import Path, PurePosixPath
import posixpath
import zipfile
import json
import re

from update_guard_dependency_recovery import encode_json, sha256


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/p0'
GUIDE = SOURCE / 'release-guide.md'
BASE = ROOT / 'releases/2026-11-23-dependency-recovery/Codex-独立审查升级源码包.zip'
VERSION = '2026.11.23-p0'
RELEASE = ROOT / 'releases/2026-11-23-p0'
ARCHIVE_NAME = 'Codex-Agent-Toolkit.zip'
GUIDE_KEY = 'guides/Codex-P0-异机安装与验收.md'
SKILL_KEY = 'implementation/skills/local-code-guard/SKILL.md'


def collect(skill):
    previous = json.loads((BASE.parent / '交付清单.json').read_text())
    if sha256(BASE.read_bytes()) != previous['archive_sha256']:
        raise ValueError('The pinned base archive differs from its release receipt.')
    with zipfile.ZipFile(BASE) as archive:
        pinned = json.loads(archive.read('PACKAGE-MANIFEST.json'))
        entries = {name: archive.read(name) for name in pinned['sha256']}
    for name, checksum in pinned['sha256'].items():
        if sha256(entries[name]) != checksum:
            raise ValueError(f'Published base member changed: {name}')
    entries.pop('Codex-异机迁移实施指南.md')
    hook_key = 'implementation/code-guard/hook.py'
    entries[hook_key] = portable_engine(entries[hook_key].decode()).encode()
    for directory in ('code-guard', 'md-governance'):
        entries.update({f'implementation/{directory}/{path.name}': path.read_bytes()
                        for path in (SOURCE / directory).glob('*.py')})
    entries['implementation/hooks.template.json'] = (SOURCE / 'hooks.template.json').read_bytes()
    entries['implementation/code-guard/README.md'] = (SOURCE / 'code-guard/README.md').read_bytes()
    entries[SKILL_KEY] = skill.read_bytes()
    entries[GUIDE_KEY] = GUIDE.read_bytes()
    entries['README.md'] = (SOURCE / 'public-readme.md').read_bytes()
    return entries


def portable_engine(source):
    extensions = re.search(r'^    extensions=(set\(.+\))$', source, re.M)
    if extensions is None:
        raise ValueError('Cannot find the pinned scanner extension set.')
    source = source.replace(extensions[0], '    extensions=SOURCE_EXTENSIONS', 1)
    state = "STATE=BASE/'state'"
    if source.count(state) != 1:
        raise ValueError('Cannot locate the pinned scanner declaration.')
    return source.replace(state, state + '\nSOURCE_EXTENSIONS = ' + extensions[1], 1)


def validate(entries):
    for name, content in entries.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError(f'Invalid release path: {name}')
        if name.endswith(('.py', '.json', '.md', '.mjs', '.txt')) and b'/Users/a0000' in content:
            raise ValueError(f'Machine-specific source in {name}')
    for name in (SKILL_KEY, 'README.md'):
        for raw in re.findall(r'\]\(([^)]+)\)', entries[name].decode()):
            link, _, anchor = raw.partition('#')
            if not link or '://' in link:
                continue
            relative = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
            if relative not in entries:
                raise ValueError(f'Broken release link in {name}: {link}')
            if anchor:
                headings = re.findall(r'^#+ (.+)$', entries[relative].decode(), re.M)
                if anchor not in {title.lower().replace(' ', '-') for title in headings}:
                    raise ValueError(f'Broken release heading in {name}: {raw}')
    template = json.loads(entries['implementation/hooks.template.json'])['hooks']
    if set(template) != {'Stop'} or 'closeout.py' not in str(template['Stop']):
        raise ValueError('Release must use the task closeout entry point.')


def build(entries, destination):
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {'version': VERSION, 'base_archive_sha256': sha256(BASE.read_bytes()),
                'sha256': {name: sha256(content) for name, content in sorted(entries.items())}}
    output = destination / ARCHIVE_NAME
    with zipfile.ZipFile(output, 'w') as archive:
        for name, content in sorted(dict(entries, **{'PACKAGE-MANIFEST.json': encode_json(manifest)}).items()):
            archive.writestr(zipfile.ZipInfo(name), content, compress_type=zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() or any(sha256(archive.read(n)) != h for n, h in manifest['sha256'].items()):
            raise ValueError('Release archive did not verify.')
    delivery = dict(manifest, archive=output.name, archive_sha256=sha256(output.read_bytes()))
    (destination / '交付清单.json').write_bytes(encode_json(delivery))
    print(output)


def export(entries, destination):
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Export to an empty staging directory.')
    for name, content in entries.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', type=Path)
    parser.add_argument('--draft-skill', type=Path, help='For isolated export tests only.')
    args = parser.parse_args()
    if args.draft_skill and not args.export:
        parser.error('Use --draft-skill only with an isolated --export.')
    entries = collect(args.draft_skill or (SOURCE / 'portable-skill.md'))
    validate(entries)
    if args.export:
        export(entries, args.export)
    else:
        build(entries, RELEASE)


if __name__ == '__main__':
    main()
