"""Assemble the portable Tauri app without any game research exports."""
import hashlib
import argparse
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, help='独立便携目录，避免覆盖运行中的旧EXE')
    parser.add_argument('--mcp-command', help='发布包中示例MCP可执行文件路径，避免写入CI runner的临时目录')
    args = parser.parse_args()
    built = ROOT / 'src-tauri' / 'target' / 'release'
    dest = args.output_dir.resolve() if args.output_dir else ROOT / 'release' / 'MMTR-Workbench'
    dest.mkdir(parents=True, exist_ok=True)
    mapping = [(built / 'mmtr-workbench.exe', 'MMTR Workbench.exe'),
               (built / 'mmtr-core.exe', 'mmtr-core.exe'),
               (ROOT / '工具使用说明.md', '使用说明.md'),
               (ROOT / 'docs' / 'game-support.md', '游戏支持说明.md')]
    for source, name in mapping:
        if not source.is_file():
            raise FileNotFoundError(f'Run npm run desktop:build first: {source}')
        shutil.copy2(source, dest / name)
    guide = dest / '使用说明.md'
    # 用户说明链接指向包内文档；开发环境说明保留仓库入口。
    guide.write_text(guide.read_text(encoding='utf-8').replace(
        '](docs/workbench-mcp.md)', '](MCP使用说明.md)').replace(
        '](docs/game-support.md)', '](游戏支持说明.md)').replace(
        '](docs/development.md)',
        '](https://github.com/Monkeysama/MMTR-Workbench/blob/main/docs/development.md)'), encoding='utf-8')
    # 配置只随便携包交付，不改客户端全局设置；移动文件夹后需更新command。
    configuration = {'mcpServers': {'mmtr-workbench': {
        'command': args.mcp_command or str((dest / 'mmtr-core.exe').resolve()), 'args': ['--mcp']}}}
    (dest / 'mcp-config.json').write_text(json.dumps(configuration, ensure_ascii=False, indent=2), encoding='utf-8')
    # MCP 说明从 docs 移到包根目录后，游戏支持链接也需指向包内名称。
    mcp_guide = (ROOT / 'docs' / 'workbench-mcp.md').read_text(encoding='utf-8')
    (dest / 'MCP使用说明.md').write_text(mcp_guide.replace(
        '](game-support.md)', '](游戏支持说明.md)'), encoding='utf-8')
    names = [name for _, name in mapping] + ['mcp-config.json', 'MCP使用说明.md']
    manifest = [{'file': name, 'size': (dest / name).stat().st_size,
                 'sha256': hashlib.sha256((dest / name).read_bytes()).hexdigest()}
                for name in names]
    (dest / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    version = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))['version']
    archive = dest.parent / f'MMTR-Workbench-{version}-windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for name in names + ['manifest.json']:
            output.write(dest / name, 'MMTR-Workbench/' + name)
    # 控制台可能是Windows cp1252；JSON日志转义非ASCII，磁盘文件仍保留UTF-8中文。
    print(json.dumps({'directory': str(dest), 'archive': str(archive), 'files': manifest}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
