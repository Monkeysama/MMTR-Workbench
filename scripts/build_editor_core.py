"""Bundle the existing Python parser/rebuilder as the Tauri JSON-lines sidecar."""
import os
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def make_icon():
    # Small code-native layered-material mark, emitted directly as a Windows ICO.
    n = 32
    pixels = bytearray()
    for y in reversed(range(n)):
        for x in range(n):
            color = (30, 41, 59, 255)
            for cy in [11, 16, 21]:
                if abs(abs(x - 15.5) / 1.8 + abs(y - cy) - 5.8) < 1.25 and abs(x - 15.5) < 11:
                    color = (219, 234, 254, 255)
            r, g, b, a = color
            pixels.extend((b, g, r, a))
    dib = struct.pack('<IiiHHIIiiII', 40, n, n * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0)
    payload = dib + pixels + bytes(128)
    icon = struct.pack('<HHH', 0, 1, 1) + struct.pack('<BBBBHHII', n, n, 0, 0, 1, 32, len(payload), 22) + payload
    target = ROOT / 'src-tauri' / 'icons' / 'icon.ico'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(icon)


def main():
    make_icon()
    deps = ROOT / '.build' / 'pyinstaller'
    if not (deps / 'PyInstaller').is_dir():
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--target', str(deps), 'pyinstaller==6.22.3'], check=True)
    # MCP官方SDK只安装在项目构建目录；执行.pth以发现Windows pywin32扩展，不污染全局Python。
    editor_deps = ROOT / '.build' / 'editor-deps'
    requirements = ROOT / 'requirements-editor.txt'
    marker = editor_deps / '.requirements-editor'
    if not marker.is_file() or marker.read_bytes() != requirements.read_bytes():
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', '--target', str(editor_deps),
                        '-r', str(requirements)], check=True)
        marker.write_bytes(requirements.read_bytes())
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(deps) + os.pathsep + str(ROOT)
    bootstrap = ('import site,runpy;site.addsitedir(' + repr(str(editor_deps)) + ');'
                 'runpy.run_module("PyInstaller",run_name="__main__")')
    subprocess.run([sys.executable, '-c', bootstrap, '--noconfirm', '--onefile', '--console', '--name', 'mmtr-core',
                    '--collect-submodules', 'mcp.server', '--collect-submodules', 'mcp.shared',
                    '--collect-data', 'mcp', '--collect-all', 'mcp_types', '--copy-metadata', 'mcp',
                    '--copy-metadata', 'mcp-types',
                    '--paths', str(ROOT), '--distpath', str(ROOT / '.build' / 'core'),
                    '--workpath', str(ROOT / '.build' / 'freeze'), '--specpath', str(ROOT / '.build'),
                    str(ROOT / 'scripts' / 'editor_core_entry.py')], cwd=ROOT, env=environment, check=True)
    host = next(line.split(':', 1)[1].strip() for line in subprocess.check_output(['rustc', '-vV'], text=True).splitlines() if line.startswith('host:'))
    destination = ROOT / 'src-tauri' / 'binaries' / f'mmtr-core-{host}.exe'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes((ROOT / '.build' / 'core' / 'mmtr-core.exe').read_bytes())
    print(destination)


if __name__ == '__main__':
    main()
