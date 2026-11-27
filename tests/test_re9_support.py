"""RE9（MMTR 251112994 / MDF 2.51 108 字节材质项）：合成拒绝用例与可选真实语料回归。

语料目录由 MMTR_TEST_RE9_NATIVES 指定（解包后的 natives）；缺少时显式 skip，不读取游戏安装目录。
"""
import unittest

from local_corpus import RE9_NATIVES
from mmtr_editor.material_extension import current_layout
from mmtr_editor.mdf import MdfDocument
from mmtr_editor.model import Document, EditError

VERSION = '.251112994'
# 真实语料中已知的保守拒绝：母版 metadata 无 UserMaterial；MDF 比母版少成员，需要先同步。
KNOWN_BINDING_REJECTIONS = ('没有 UserMaterial', '请先同步已有接口')


class Re9SyntheticTests(unittest.TestCase):
    def test_master_without_user_material_metadata_rejects_with_specific_reason(self):
        info = {'material_header_bytes': 0, 'metadata': {'cbuffers': [{'name': 'SceneInfo', 'buffer_size': 624,
                                                                      'members_offset': 0, 'member_count': 0,
                                                                      'members': []}]}}
        material = {'textures': [], 'gpbf': [], 'properties': [], 'property_block_size': 0}
        with self.assertRaisesRegex(EditError, '没有 UserMaterial'):
            current_layout(object(), material, info)


@unittest.skipUnless((RE9_NATIVES / 'STM').is_dir(), 'RE9 natives 语料不可用（MMTR_TEST_RE9_NATIVES）')
class Re9CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mmtrs = sorted(RE9_NATIVES.rglob('*.mmtr' + VERSION))
        cls.mdfs = sorted(RE9_NATIVES.rglob('*.mdf2.51'))

    def test_every_mmtr_is_re9_without_material_header_and_round_trips(self):
        self.assertTrue(self.mmtrs)
        for path in self.mmtrs:
            data = path.read_bytes()
            doc = Document(data, path)
            info = doc.info
            self.assertEqual((info['game'], info['material_header_bytes'], info['header_layout']),
                             ('RE9', 0, 'per-target'), path)
            self.assertEqual(doc.build()[0], data, path)

    def test_every_mdf_uses_108_byte_layout_and_round_trips(self):
        self.assertTrue(self.mdfs)
        for path in self.mdfs:
            data = path.read_bytes()
            mdf = MdfDocument(data, path)
            self.assertEqual(mdf.MATERIAL_SIZE, 108, path)
            self.assertEqual(mdf.build()[0], data, path)

    def test_mdf_fields_match_master_user_material_or_reject_conservatively(self):
        masters, matched = {}, 0
        for path in self.mdfs:
            mdf = MdfDocument(path.read_bytes(), path)
            for material in mdf.materials:
                source = RE9_NATIVES / 'STM' / (material['master'] + VERSION)
                if not material['master'] or not source.is_file():
                    continue
                if source not in masters:
                    masters[source] = Document(source.read_bytes(), source)
                try:
                    layout = current_layout(masters[source], material)
                except EditError as exc:
                    self.assertTrue(any(text in str(exc) for text in KNOWN_BINDING_REJECTIONS), f'{path}: {exc}')
                    continue
                self.assertEqual((layout.header_bytes, layout.parameter_bytes),
                                 (0, material['property_block_size']), path)
                matched += 1
        self.assertGreater(matched, 0)


if __name__ == '__main__':
    unittest.main()
