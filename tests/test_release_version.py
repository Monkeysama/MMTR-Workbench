"""tag版本必须覆盖全部产品入口，同时保持依赖、源码和README原样。"""
import json
from pathlib import Path
import shutil
import tempfile
import tomllib
import unittest

from scripts.set_release_version import parse_tag, set_version

ROOT = Path(__file__).resolve().parents[1]
FILES = ('package.json', 'package-lock.json', 'src-tauri/tauri.conf.json',
         'src-tauri/Cargo.toml', 'src-tauri/Cargo.lock', 'mmtr_editor/__init__.py')


class ReleaseVersionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for name in FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        (self.root / 'README.md').write_bytes(b'readme stays unchanged\n')

    def snapshot(self):
        return {name: (self.root / name).read_bytes() for name in (*FILES, 'README.md')}

    def test_stable_tag_synchronizes_all_versions_and_preserves_dependencies(self):
        before = self.snapshot()
        result = set_version(self.root, 'v1.0.0')
        self.assertEqual(result, {'version': '1.0.0', 'prerelease': False})
        for name in ('package.json', 'package-lock.json', 'src-tauri/tauri.conf.json'):
            data = json.loads((self.root / name).read_text(encoding='utf-8'))
            self.assertEqual(data['version'], '1.0.0')
        lock = json.loads((self.root / 'package-lock.json').read_text(encoding='utf-8'))
        self.assertEqual(lock['packages']['']['version'], '1.0.0')
        old_lock = json.loads(before['package-lock.json'])
        self.assertEqual({k: v for k, v in lock['packages'].items() if k},
                         {k: v for k, v in old_lock['packages'].items() if k})
        cargo = tomllib.loads((self.root / 'src-tauri/Cargo.toml').read_text(encoding='utf-8'))
        self.assertEqual(cargo['package']['version'], '1.0.0')
        packages = tomllib.loads((self.root / 'src-tauri/Cargo.lock').read_text(encoding='utf-8'))['package']
        self.assertEqual(next(p['version'] for p in packages if p['name'] == 'mmtr-workbench'), '1.0.0')
        old_packages = tomllib.loads(before['src-tauri/Cargo.lock'].decode())['package']
        self.assertEqual([p for p in packages if p['name'] != 'mmtr-workbench'],
                         [p for p in old_packages if p['name'] != 'mmtr-workbench'])
        namespace = {}
        exec((self.root / 'mmtr_editor/__init__.py').read_text(encoding='utf-8'), namespace)
        self.assertEqual(namespace['__version__'], '1.0.0')
        self.assertEqual((self.root / 'README.md').read_bytes(), before['README.md'])
        stamped = self.snapshot()
        set_version(self.root, 'v1.0.0')
        self.assertEqual(self.snapshot(), stamped)

    def test_prerelease_and_build_identifiers(self):
        for tag in ('v1.2.3-rc.1', 'v1.2.3-0', 'v1.2.3-beta.2+build.7'):
            with self.subTest(tag=tag):
                self.assertEqual(set_version(self.root, tag), {'version': tag[1:], 'prerelease': True})
        self.assertFalse(parse_tag('v1.2.3+build.7')['prerelease'])

    def test_invalid_tags_never_write_version_files(self):
        before = self.snapshot()
        for tag in ('1.0.0', 'v1.0', 'v01.0.0', 'v1.0.0-01', 'v1.0.0-rc..1',
                    'v1.0.0\n', 'v1.0.0/../../other', 'v1.0.0;echo bad'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                set_version(self.root, tag)
            self.assertEqual(self.snapshot(), before)

    def test_missing_or_ambiguous_source_fails_before_any_write(self):
        path = self.root / 'src-tauri/Cargo.lock'
        path.write_text('version = 4\n', encoding='utf-8')
        before = self.snapshot()
        with self.assertRaises((ValueError, KeyError)):
            set_version(self.root, 'v1.0.0')
        self.assertEqual(self.snapshot(), before)
