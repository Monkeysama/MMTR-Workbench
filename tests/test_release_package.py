"""发布完整性检查需拒绝包体篡改、ZIP额外文件和清单不一致。"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

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
