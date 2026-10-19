"""发布前核对ZIP与manifest，生成下载校验文件；只读取包体并写入校验和。"""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


# 所有声明文件必须存在且与ZIP逐字节相同，额外产物不得混入发布包。
def verify_package(directory, archive):
    manifest_bytes = (directory / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    expected = {'MMTR-Workbench/' + entry['file'] for entry in manifest}
    expected.add('MMTR-Workbench/manifest.json')
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.namelist()) != len(expected) or set(bundle.namelist()) != expected:
            raise ValueError('ZIP文件清单与manifest不一致。')
        if bundle.read('MMTR-Workbench/manifest.json') != manifest_bytes:
            raise ValueError('ZIP中的manifest与包目录不一致。')
        for entry in manifest:
            name = entry['file']
            if Path(name).name != name:
                raise ValueError('manifest只能包含包根目录文件。')
            data = (directory / name).read_bytes()
            if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError(f'包体校验失败：{name}')
            if bundle.read('MMTR-Workbench/' + name) != data:
                raise ValueError(f'ZIP内容不一致：{name}')
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    from set_release_version import parse_tag
    version = parse_tag('v' + args.version)['version']
    archive = ROOT / 'release' / f'MMTR-Workbench-{version}-windows-x64.zip'
    digest = verify_package(ROOT / 'release' / 'MMTR-Workbench', archive)
    checksum = archive.parent / 'SHA256SUMS.txt'
    checksum.write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    print(f'PASS {archive.name}: {digest}')


if __name__ == '__main__':
    main()
