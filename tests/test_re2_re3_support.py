"""RE2 / RE3 的无运行时缓存键 MMTR 布局；只用合成数据。"""
from pathlib import Path
import unittest

from inspect_mmtr import inspect_data
from mmtr_editor.model import Document, EditError
from test_structure import fixture


PATHS = {
    1808160001: (Path('natives/stm/character/player/synthetic.mmtr.1808160001'), 'RE2'),
    1905100741: (Path('natives/stm/character/player/synthetic.mmtr.1905100741'), 'RE3'),
}


class Re2Re3MmtrTests(unittest.TestCase):
    def test_four_group_layouts_parse_without_runtime_keys(self):
        for version, (path, game) in PATHS.items():
            with self.subTest(version=version):
                data = fixture(4, 1, record_size=0x108, runtime_cache_keys=False)
                _, info = inspect_data(data, path)
                self.assertEqual((info['game'], info['runtime_cache_keys']), (game, False))
                self.assertEqual((info['record_count'], info['shader_count']), (4, 1))
                self.assertEqual(info['shaders'][0]['trailer_offset'], len(data))

    def test_rename_rebuild_keeps_layout(self):
        for version, (path, game) in PATHS.items():
            with self.subTest(version=version):
                data = fixture(4, 1, record_size=0x108, runtime_cache_keys=False)
                doc = Document(data, path)
                doc.rename(doc.info['records'][0]['name_offset'], 'Test')
                rebuilt, report = doc.build()
                self.assertTrue(report['reparse_valid'])
                self.assertEqual(len(rebuilt), len(data))
                _, info = inspect_data(rebuilt, path)
                self.assertEqual((info['game'], info['records'][0]['name']), (game, 'Test'))

    def test_group_count_is_enforced(self):
        for version, (path, _game) in PATHS.items():
            with self.subTest(version=version):
                with self.assertRaisesRegex(EditError, 'RE2 四组|RE3 四组'):
                    Document(fixture(3, 1, record_size=0x108, runtime_cache_keys=False), path)


if __name__ == '__main__':
    unittest.main()
