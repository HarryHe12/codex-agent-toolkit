"""Apply the same recovery diagnostics to installed and portable hook sources."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile


ROOT = Path(__file__).resolve().parent
DEPENDENCY_SOURCE = ROOT / 'code_guard/scanner_dependencies.py'
RECOVERY_GUIDE = """缺少 Node.js 或扫描器的 npm 包时，门禁会报告缺失项和安装步骤。
包缺失时，按提示在门禁目录用锁文件安装。有安装权限的 Agent 可直接安装并重试。
需要用户安装时，安装完成后在原聊天回复「继续」，重跑原验证并恢复任务。
如果首次建立编辑前基线时失败，先补齐依赖并重试初始化，再编辑。
"""


def update_hook(source, *, local):
    if 'class ScannerDependencyError' in source:
        raise ValueError('Start with the verified pre-recovery hook source.')
    dependencies = DEPENDENCY_SOURCE.read_text().split('class ScannerDependencyError', 1)[1]
    imports = 'from pathlib import Path\nimport shlex\n'
    if local:
        imports += 'import shutil\n'
    source = source.replace('\nBASE=', '\n' + imports + '\nBASE=', 1)
    source = source.replace('\ndef verify_integrity():',
                            '\n\nclass ScannerDependencyError' + dependencies + '\ndef verify_integrity():', 1)
    source = re.sub(
        r"(def run_scan\([^\n]+\):\n)(    if not NODE: raise RuntimeError[^\n]*\n)?",
        r'\1    check_dependencies(BASE, NODE)\n', source, count=1,
    )
    source = re.sub(
        r'(if (?:p|scan)\.returncode:\s*)raise RuntimeError\((p|scan)\.stderr\[-3000:\]\)',
        r'\1raise_scan_error(BASE, NODE, \2.stderr)', source, count=1,
    )
    if local:
        source = re.sub(r'^NODE=(.+)$', r"NODE=os.environ.get('CODEX_GUARD_NODE') or shutil.which(\1) or shutil.which('node')",
                        source, count=1, flags=re.M)
    assert '    check_dependencies(BASE, NODE)' in source
    assert 'raise_scan_error(BASE, NODE,' in source
    return source


def update_closeout(source):
    before = "    receipt = baseline.get('receipt', {})"
    assert source.count(before) == 1
    return source.replace(before, "    if any(relevant(name) and not prose(name) for name in differences):\n"
                          "        hook.check_dependencies(hook.BASE, hook.NODE)\n" + before)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def update_release(archive, destination):
    with zipfile.ZipFile(archive) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(entries['PACKAGE-MANIFEST.json'])
    for name, expected in manifest['sha256'].items():
        assert sha256(entries[name]) == expected, name
    hook = 'implementation/code-guard/hook.py'
    entries[hook] = update_hook(entries[hook].decode(), local=False).encode()
    manifest['version'] += '-dependency-recovery'
    manifest['sha256'][hook] = sha256(entries[hook])
    for name in entries:
        if name.endswith('Codex-异机迁移实施指南.md'):
            guide = entries[name].decode()
            anchor = '## 4. 本地代码门禁如何工作'
            if anchor in guide:
                entries[name] = guide.replace(anchor, RECOVERY_GUIDE + '\n' + anchor, 1).encode()
                manifest['sha256'][name] = sha256(entries[name])
    entries['PACKAGE-MANIFEST.json'] = encode_json(manifest)
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / archive.name
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as package:
        for name, content in sorted(entries.items()):
            package.writestr(name, content)
    with zipfile.ZipFile(output) as package:
        assert package.testzip() is None
        for name, expected in manifest['sha256'].items():
            assert sha256(package.read(name)) == expected, name
    delivery = dict(manifest, archive=output.name, archive_sha256=sha256(output.read_bytes()))
    (destination / '交付清单.json').write_bytes(encode_json(delivery))


def encode_json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    update_release(args.archive, args.destination)


if __name__ == '__main__':
    main()
