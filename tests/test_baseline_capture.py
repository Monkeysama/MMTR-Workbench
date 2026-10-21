"""基线工具的安全与回归判定；合成输入无需游戏资产或 DXC。"""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.capture_mmtr_baseline import (assert_material_preserved, capture, compare,
                                          digest, fingerprint, verified_input)


class BaselineCaptureTests(unittest.TestCase):
    # 输入被替换时必须在解析和创建输出前失败，不能自动采信新的 hash。
    def test_changed_input_rejected_before_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / 'input'
            source.write_bytes(b'changed')
            entry = {'path': 'input', 'sha256': digest(b'original')}
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'schema': 1, 'samples': [{'id': 'sample', 'mmtr': entry}]}))
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                capture(manifest, root / 'out', 1)
            self.assertFalse((root / 'out').exists())
            entry['sha256'] = digest(b'changed')
            self.assertEqual(verified_input(root, entry), (source, b'changed'))

    def test_missing_optional_is_reported_and_not_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'schema': 1, 'samples': [{'id': 'missing', 'optional': True,
                'mmtr': {'path': 'absent', 'sha256': '0' * 64}}]}))
            report = capture(manifest, root / 'out', 1)
            self.assertFalse(report['passed'])
            self.assertEqual(report['skipped'][0]['sample'], 'missing')

    def test_path_traversal_sample_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'schema': 1, 'samples': [{'id': '../escape'}]}))
            with self.assertRaisesRegex(ValueError, 'id'):
                capture(manifest, root / 'out', 1)

    def test_protected_input_and_existing_output_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'input').write_bytes(b'fixed')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'schema': 1, 'samples': [{'id': 'a',
                'mmtr': {'path': 'input', 'sha256': digest(b'fixed')}}]}))
            with self.assertRaisesRegex(ValueError, '保护'):
                capture(manifest, root, 1)
            folder = root / 'existing'
            folder.mkdir()
            (folder / 'sentinel').write_bytes(b'keep')
            with self.assertRaises(FileExistsError):
                capture(manifest, folder, 1)
            self.assertEqual((folder / 'sentinel').read_bytes(), b'keep')
            self.assertEqual((root / 'input').read_bytes(), b'fixed')

    def test_failed_parse_records_failed_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'input').write_bytes(b'fixed')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'schema': 1, 'samples': [{'id': 'a',
                'mmtr': {'path': 'input', 'sha256': digest(b'fixed')}}]}))
            with patch('scripts.capture_mmtr_baseline.Document', side_effect=ValueError('bad structure')):
                with self.assertRaisesRegex(ValueError, 'bad structure'):
                    capture(manifest, root / 'out', 1)
            report = json.loads((root / 'out/baseline.json').read_text())
            self.assertFalse(report['passed'])
            self.assertTrue(report['sources_preserved'])
            self.assertIn('bad structure', report['error'])

    def test_semantics_compared_independently_of_timing(self):
        old = {'manifest_sha256': 'same', 'passed': True, 'sources_preserved': True, 'skipped': [],
               'cases': [{'sample': 'a', 'scenario': 'noop', 'fingerprint': fingerprint({'x': 1}),
                          'median_seconds': 4.0}]}
        new = copy.deepcopy(old)
        new['cases'][0]['median_seconds'] = 1.0
        self.assertTrue(compare(old, new)[0]['semantic_equal'])
        new['cases'][0]['fingerprint'] = fingerprint({'x': 2})
        self.assertFalse(compare(old, new)[0]['semantic_equal'])
        new['manifest_sha256'] = 'different'
        with self.assertRaisesRegex(ValueError, 'manifest'):
            compare(old, new)
        new = copy.deepcopy(old)
        new['skipped'] = ['missing']
        with self.assertRaisesRegex(ValueError, '完整'):
            compare(old, new)

    def test_existing_values_and_other_materials_are_protected(self):
        old = SimpleNamespace(materials=[{'index': 0, 'name': 'linked', 'properties': [{'values': [0.5]}],
                'textures': [{'name': 'map', 'path': 'old'}], 'gpbf': []},
                {'index': 1, 'name': 'other'}])
        new = copy.deepcopy(old)
        new.materials[0]['properties'].append({'values': [0.0]})
        assert_material_preserved(old, new, [0])
        new.materials[0]['textures'][0]['path'] = 'wrong'
        with self.assertRaisesRegex(AssertionError, 'textures'):
            assert_material_preserved(old, new, [0])
        new = copy.deepcopy(old)
        new.materials[1]['name'] = 'wrong'
        with self.assertRaisesRegex(AssertionError, '非关联'):
            assert_material_preserved(old, new, [0])


if __name__ == '__main__':
    unittest.main()
