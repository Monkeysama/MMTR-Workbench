"""RE9（MMTR 251112994 / MDF 2.51 108 字节材质项）：合成拒绝用例与可选真实语料回归。

语料目录由 MMTR_TEST_RE9_NATIVES 指定（解包后的 natives）；缺少时显式 skip，不读取游戏安装目录。
"""
import os
from pathlib import Path
import shutil
import tempfile
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


MASTER = 'MaterialShader/Master/Env_Default_Emissive.mmtr'
MDF = 'Character/ch/ch01/0100/09/00/ch0100_09_00_00.mdf2.51'
DXC = Path(os.environ.get('MMTR_DXC_PATH', ''))


# 新增后再删除应回到原版布局：覆盖 RE9 局部数组描述符链、meshlet 网格表与几何记录、CAPCOM 保留成员。
@unittest.skipUnless((RE9_NATIVES / 'STM' / (MASTER + VERSION)).is_file() and DXC.is_file(),
                     'RE9 natives 语料或 DXC 不可用（MMTR_TEST_RE9_NATIVES / MMTR_DXC_PATH）')
class Re9NativeRoundTripTests(unittest.TestCase):
    def test_added_parameter_and_texture_can_be_removed_back_to_original_layout(self):
        from mmtr_editor.material_removal_service import analyze_removal_preview, apply_material_removal
        from mmtr_editor.rpc import Service
        with tempfile.TemporaryDirectory() as folder:
            stm = Path(folder) / 'natives' / 'STM'
            for relative in (MASTER + VERSION, MDF):
                (stm / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(RE9_NATIVES / 'STM' / relative, stm / relative)
            original = MdfDocument.open(stm / MDF).material(6)
            original_members = [cb['members'] for cb in Document.open(stm / (MASTER + VERSION)).info['metadata']['cbuffers']
                                if cb['name'] == 'UserMaterial'][0]
            service = Service()
            service.handle({'op': 'open', 'path': str(stm / (MASTER + VERSION))})
            service.handle({'op': 'mdf_open', 'path': str(stm / MDF), 'material_index': 6})
            add = [{'action': 'parameter', 'name': 'Re9ProbeParam', 'byte_size': 16, 'byte_offset': 224},
                   {'action': 'texture', 'name': 'Re9ProbeTex'}]
            plan = service.handle({'op': 'material_plan', 'dll': str(DXC), 'actions': add, 'record_index': 7})
            service.handle({'op': 'material_apply', 'dll': str(DXC), 'actions': add, 'record_index': 7,
                            'expected_plan': plan['digest']})
            remove = [{'action': 'parameter', 'name': 'Re9ProbeParam'}, {'action': 'texture', 'name': 'Re9ProbeTex'}]
            preview = analyze_removal_preview(service, str(DXC), remove, 6, 0)
            self.assertTrue(preview['eligible'], preview['failures'][:3])
            apply_material_removal(service, str(DXC), remove, 6, 0, expected_plan=preview['plan']['digest'])
            out = Path(folder) / 'out' / 'natives' / 'STM'
            for relative in (MASTER + VERSION, MDF):
                (out / relative).parent.mkdir(parents=True, exist_ok=True)
            service.handle({'op': 'export', 'path': str(out / (MASTER + VERSION)), 'dll': str(DXC)})
            service.handle({'op': 'mdf_export', 'path': str(out / MDF)})
            reopened = Service()
            reopened.handle({'op': 'open', 'path': str(out / (MASTER + VERSION))})
            reopened.handle({'op': 'mdf_open', 'path': str(out / MDF), 'material_index': 6})
            validation = reopened.handle({'op': 'mmtr_validate_interfaces', 'scope': 'associated_layout',
                                          'dll': str(DXC)})
            self.assertTrue(validation['valid'], validation['issues'][:3])
            material = reopened.mdf.material(6)
            self.assertEqual([(p['name'], p['offset'], p['raw_hex']) for p in material['properties']],
                             [(p['name'], p['offset'], p['raw_hex']) for p in original['properties']])
            self.assertEqual([(t['name'], t['path']) for t in material['textures']],
                             [(t['name'], t['path']) for t in original['textures']])
            members = [cb['members'] for cb in reopened.doc.info['metadata']['cbuffers'] if cb['name'] == 'UserMaterial'][0]
            self.assertEqual([(m['name'], m['byte_offset'], m['byte_size']) for m in members],
                             [(m['name'], m['byte_offset'], m['byte_size']) for m in original_members])


if __name__ == '__main__':
    unittest.main()
