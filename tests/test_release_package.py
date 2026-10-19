"""发布完整性检查需拒绝包体篡改、ZIP额外文件和清单不一致。"""
import hashlib
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import package_editor
from scripts.verify_release_package import verify_package


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.directory = self.root / 'portable'
        self.directory.mkdir()
        self.archive = self.root / 'portable.zip'
        payload = b'synthetic executable placeholder'
        (self.directory / 'mmtr-core.exe').write_bytes(payload)
        manifest = [{'file': 'mmtr-core.exe', 'size': len(payload),
                     'sha256': hashlib.sha256(payload).hexdigest()}]
        (self.directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        with zipfile.ZipFile(self.archive, 'w') as bundle:
            for name in ('mmtr-core.exe', 'manifest.json'):
                bundle.write(self.directory / name, 'MMTR-Workbench/' + name)

    def test_matching_archive_has_download_checksum(self):
        self.assertEqual(verify_package(self.directory, self.archive),
                         hashlib.sha256(self.archive.read_bytes()).hexdigest())

    def test_tampered_payload_is_rejected(self):
        (self.directory / 'mmtr-core.exe').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, '校验失败'):
            verify_package(self.directory, self.archive)

    def test_extra_archive_file_is_rejected(self):
        with zipfile.ZipFile(self.archive, 'a') as bundle:
            bundle.writestr('unexpected.dll', b'extra')
        with self.assertRaisesRegex(ValueError, '清单'):
            verify_package(self.directory, self.archive)

    def test_changed_manifest_is_rejected(self):
        (self.directory / 'manifest.json').write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            verify_package(self.directory, self.archive)


# 使用合成EXE验证非UTF-8控制台；不编译、运行或打包本机已有应用。
class PortablePackageConsoleTests(unittest.TestCase):
    def test_chinese_filenames_work_with_cp1252_stdout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / '发布测试'
            built = root / 'src-tauri' / 'target' / 'release'
            built.mkdir(parents=True)
            (root / 'docs').mkdir()
            for name in ('mmtr-workbench.exe', 'mmtr-core.exe'):
                (built / name).write_bytes(b'synthetic executable placeholder')
            (root / 'package.json').write_text('{"version":"0.3.0"}', encoding='utf-8')
            (root / '工具使用说明.md').write_text('[MCP](docs/workbench-mcp.md)', encoding='utf-8')
            (root / 'docs' / 'workbench-mcp.md').write_text('中文说明', encoding='utf-8')
            buffer = io.BytesIO()
            stream = io.TextIOWrapper(buffer, encoding='cp1252', errors='strict')
            with patch.object(package_editor, 'ROOT', root), patch('sys.argv', ['package_editor.py']), redirect_stdout(stream):
                package_editor.main()
            stream.flush()
            report = json.loads(buffer.getvalue().decode('cp1252'))
            directory, archive = Path(report['directory']), Path(report['archive'])
            self.assertTrue(verify_package(directory, archive))
            self.assertEqual((directory / 'MCP使用说明.md').read_text(encoding='utf-8'), '中文说明')
            self.assertIn('](MCP使用说明.md)', (directory / '使用说明.md').read_text(encoding='utf-8'))
            with zipfile.ZipFile(archive) as bundle:
                self.assertIn('MMTR-Workbench/使用说明.md', bundle.namelist())
