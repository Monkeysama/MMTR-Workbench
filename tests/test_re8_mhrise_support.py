"""RE8 / MHRISE / RE2RT 的无运行时缓存键 MMTR 布局；只用合成数据。"""
from pathlib import Path
import unittest

from inspect_mmtr import inspect_data
from mmtr_editor.model import Document, EditError
from mmtr_editor.structure import parse_structure
from test_structure import fixture


PATHS = {
    2102188797: (Path('natives/stm/character/player/synthetic.mmtr.2102188797'), 'RE8', 5),
    220427553: (Path('natives/stm/character/player/synthetic.mmtr.220427553'), 'MHRISE', 3),
    2109101635: (Path('natives/stm/character/player/synthetic.mmtr.2109101635'), 'RE2RT', 5),
}


class LegacyMmtrTests(unittest.TestCase):
    def test_no_key_layouts_parse_without_consuming_next_shader(self):
        for version, (path, game, groups) in PATHS.items():
            with self.subTest(version=version):
                data = fixture(groups, 1, record_size=0x108, runtime_cache_keys=False)
                _, info = inspect_data(data, path)
                self.assertEqual((info['game'], info['runtime_cache_keys']), (game, False))
                self.assertEqual((info['record_count'], info['shader_count']), (groups, 1))
                shader = info['shaders'][0]
                self.assertEqual((shader['trailer_hex'], shader['trailer_offset']), ('', shader['offset'] + 72))
                self.assertEqual(shader['trailer_offset'], len(data))

    def test_build_keeps_shader_contiguous_without_cache_key(self):
        path, _game, groups = PATHS[2102188797]
        data = fixture(groups, 1, record_size=0x108, runtime_cache_keys=False)
        doc = Document(data, path)
        self.assertEqual(doc.integrity['shader_trailers_checked'], 0)
        doc.rename(doc.info['records'][0]['name_offset'], 'Test')
        rebuilt, report = doc.build()
        self.assertEqual((len(rebuilt), report['reparse_valid']), (len(data), True))
        _, info = inspect_data(rebuilt, path)
        self.assertEqual(info['records'][0]['name'], 'Test')
        self.assertFalse(info['runtime_cache_keys'])
        shader = info['shaders'][0]
        self.assertEqual(shader['trailer_offset'], len(rebuilt))
        self.assertEqual(rebuilt[shader['offset']:shader['trailer_offset']],
                         data[doc.info['shaders'][0]['offset']:doc.info['shaders'][0]['trailer_offset']])

    def test_declared_group_counts_are_enforced(self):
        for version, (path, _game, groups) in PATHS.items():
            with self.subTest(version=version):
                wrong = 3 if groups == 5 else 5
                with self.assertRaisesRegex(EditError, 'RE8 五组|RE2RT 五组|MHRISE 三组'):
                    Document(fixture(wrong, 1, record_size=0x108, runtime_cache_keys=False), path)

    def test_structure_does_not_claim_state_cache_keys(self):
        path, _game, groups = PATHS[220427553]
        report = parse_structure(fixture(groups, 1, record_size=0x108, runtime_cache_keys=False), path)
        self.assertFalse(any(o.kind.endswith('_cache_key') for o in report.objects))
        self.assertEqual(report.report()['state_cache_key_algorithm'], 'absent')


if __name__ == '__main__':
    unittest.main()
