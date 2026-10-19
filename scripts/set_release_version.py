"""从v开头的SemVer tag同步构建版本；仅修改本次检出的版本文件，不提交或推送。"""
import argparse
import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]
NUMBER = r'(?:0|[1-9][0-9]*)'
TAG = re.compile(
    rf'^v(?P<version>{NUMBER}\.{NUMBER}\.{NUMBER}'
    r'(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
    r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?)$')


# 拒绝路径、换行及非SemVer输入；数字预发布标识不能带前导零。
def parse_tag(tag):
    match = TAG.fullmatch(tag)
    if not match or any(part.isdigit() and len(part) > 1 and part.startswith('0')
                        for part in (match['prerelease'] or '').split('.')):
        raise ValueError('版本tag必须符合SemVer，例如v1.0.0或v1.1.0-rc.1。')
    return {'version': match['version'], 'prerelease': bool(match['prerelease'])}


# 只更新唯一匹配项；结构异常时在写任何文件前失败，避免部分版本漂移。
def replace_one(text, pattern, replacement):
    updated, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f'版本字段应唯一，实际匹配{count}项。')
    return updated


def json_version(text, version, lock=False):
    data = json.loads(text)
    if 'version' not in data:
        raise ValueError('缺少JSON版本字段。')
    data['version'] = version
    if lock:
        data['packages']['']['version'] = version
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n'


# 通过TOML解析确认包身份，文本替换仅触及本项目的版本，不重写依赖锁。
def cargo_version(text, version, lock=False):
    data = tomllib.loads(text)
    if lock:
        packages = [p for p in data['package'] if p['name'] == 'mmtr-workbench']
        if len(packages) != 1:
            raise ValueError('Cargo.lock中本项目包不唯一。')
        blocks = list(re.finditer(r'^\[\[package\]\]\n[\s\S]*?(?=^\[\[package\]\]|\Z)', text, re.MULTILINE))
        block = next(m for m in blocks if re.search(r'^name = "mmtr-workbench"$', m[0], re.MULTILINE))
    else:
        if data['package']['name'] != 'mmtr-workbench':
            raise ValueError('Cargo.toml不是Workbench包。')
        block = re.search(r'^\[package\]\n[\s\S]*?(?=^\[|\Z)', text, re.MULTILINE)
        if block is None:
            raise ValueError('Cargo.toml缺少package段。')
    updated = replace_one(block[0], r'^(version\s*=\s*)"[^"\n]+"',
                          lambda m: m[1] + json.dumps(version))
    return text[:block.start()] + updated + text[block.end():]


def set_version(root, tag):
    release = parse_tag(tag)
    version = release['version']
    transforms = {
        'package.json': lambda text: json_version(text, version),
        'package-lock.json': lambda text: json_version(text, version, lock=True),
        'src-tauri/tauri.conf.json': lambda text: json_version(text, version),
        'src-tauri/Cargo.toml': lambda text: cargo_version(text, version),
        'src-tauri/Cargo.lock': lambda text: cargo_version(text, version, lock=True),
        'mmtr_editor/__init__.py': lambda text: replace_one(
            text, r'^__version__[ \t]*=[ \t]*[\'"][^\'"\n]+[\'"][ \t]*$',
            lambda _: '__version__ = ' + repr(version)),
    }
    candidates = {name: transform((root / name).read_text(encoding='utf-8'))
                  for name, transform in transforms.items()}
    for name, text in candidates.items():
        (root / name).write_text(text, encoding='utf-8', newline='\n')
    return release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()
    release = set_version(ROOT, args.tag)
    if args.github_output:
        with args.github_output.open('a', encoding='utf-8') as output:
            output.write(f'version={release["version"]}\nprerelease={str(release["prerelease"]).lower()}\n')
    print(json.dumps(release, ensure_ascii=False))


if __name__ == '__main__':
    main()
