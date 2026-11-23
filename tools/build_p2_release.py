"""Add opt-in acceptance evidence to the verified P1 release."""
import argparse
import json
from pathlib import Path
import zipfile

from build_p1_release import (ARCHIVE_NAME, MANIFEST, ROOT, RELEASE as P1, build,
                              digest, export, json_bytes, replace, validate)


SOURCE = ROOT / 'tools/p2'
VERSION = '2026.11.23-p2'
RELEASE = ROOT / 'releases/2026-11-23-p2'


def closeout_overlay(source):
    source = replace(source, 'import hook\nfrom turn_evidence',
                     "import hook\nsys.path.insert(0, str(hook.BASE.parent / 'toolkit'))\n"
                     'import acceptance\nfrom turn_evidence')
    source = replace(source, '    differences = {}\n    for name in sorted(current.keys() | before.keys()):',
                     '    differences = {}\n    acceptance_inputs = acceptance.tracked_paths(root)\n'
                     '    for name in sorted(current.keys() | before.keys()):')
    source = replace(source, '        if present != previous and relevant(name):',
                     '        if present != previous and (relevant(name) or name in acceptance_inputs):')
    source = replace(source, '    code = any(not prose(name) for name in differences)',
                     '    code = any(relevant(name) and not prose(name) for name in differences)')
    source = replace(source, 'def verify(root, record, review, command, docs_only):',
                     'def verify(root, record, review, command, docs_only, *, run_acceptance=False):')
    source = replace(source, '    errors = code_diagnostics(root, baseline, differences)\n',
                     '    acceptance.require_execution_choice(root, differences, run_acceptance)\n'
                     '    errors = code_diagnostics(root, baseline, differences)\n')
    source = replace(source, '    if changed(root, baseline) != differences:',
                     '    acceptance_report = acceptance.verify(root, differences)\n'
                     '    if changed(root, baseline) != differences:')
    source = replace(source, "                           'docs_only': docs_only}\n    hook.save_json(record, baseline)",
                     "                           'docs_only': docs_only}\n    if acceptance_report is not None:\n"
                     "        baseline['receipt']['acceptance'] = acceptance_report\n    hook.save_json(record, baseline)")
    source = replace(source, '    project_evidence(root, session, differences)',
                     "    acceptance.require_current(root, differences, receipt.get('acceptance'))\n"
                     '    project_evidence(root, session, differences)')
    source = replace(source, "    verification.add_argument('--docs-only', action='store_true')",
                     "    verification.add_argument('--docs-only', action='store_true')\n"
                     "    verification.add_argument('--acceptance', action='store_true')")
    return replace(source, '        verify(root, record, args.review, command, args.docs_only)',
                   '        verify(root, record, args.review, command, args.docs_only, run_acceptance=args.acceptance)')


def guard_overlay(source):
    source = replace(source, "            command.add_argument('--docs-only', action='store_true')",
                     "            command.add_argument('--docs-only', action='store_true')\n"
                     "            command.add_argument('--acceptance', action='store_true')")
    return replace(source, '    closeout.verify(root, path, args.review, command, args.docs_only)',
                   '    closeout.verify(root, path, args.review, command, args.docs_only, run_acceptance=args.acceptance)')


def collect():
    archive = P1 / ARCHIVE_NAME
    expected = json.loads((P1 / '交付清单.json').read_text())['archive_sha256']
    if digest(archive.read_bytes()) != expected:
        raise ValueError('The P1 archive differs from its release receipt.')
    with zipfile.ZipFile(archive) as source:
        hashes = json.loads(source.read(MANIFEST))['sha256']
        entries = {name: source.read(name) for name in hashes}
    if any(digest(content) != hashes[name] for name, content in entries.items()):
        raise ValueError('A P1 archive member differs from its manifest.')
    closeout = 'implementation/code-guard/closeout.py'
    entries[closeout] = closeout_overlay(entries[closeout].decode()).encode()
    guard = 'implementation/toolkit/guard.py'
    entries[guard] = guard_overlay(entries[guard].decode()).encode()
    for path in SOURCE.glob('acceptance*.py'):
        entries[f'implementation/toolkit/{path.name}'] = path.read_bytes()
    entries['examples/acceptance.json'] = (SOURCE / 'examples/acceptance.json').read_bytes()
    entries['guides/Codex-P2-行为验收.md'] = (SOURCE / 'guide.md').read_bytes()
    entries['README.md'] = (SOURCE / 'public-readme.md').read_bytes()
    validate(entries)
    manifest = {'version': VERSION, 'base_archive_sha256': expected,
                'sha256': {name: digest(data) for name, data in sorted(entries.items())}}
    return dict(entries, **{MANIFEST: json_bytes(manifest)})


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
