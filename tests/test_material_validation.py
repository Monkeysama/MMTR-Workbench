"""验证覆盖语义及关联布局只读证明；原生 DXC 始终在工作进程执行。"""
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from mmtr_editor.dxc_process import compile_hlsl
from mmtr_editor.material_validation import validate_associated_layout
from mmtr_editor.mdf import MdfDocument
from mmtr_editor.model import Document, EditError
from mmtr_editor.rpc import Service
from test_mdf_editing import fixture as mdf_fixture
from test_structure import fixture as mmtr_fixture
from test_material_textures import DXC, NAMED, HEAP, source_with


class ValidationScopeTests(unittest.TestCase):
    def test_no_metadata_edits_skips_without_loading_dxc(self):
        service = Service()
        service.doc = Document(mmtr_fixture(), Path('MaterialShader/test.mmtr.240906211'))
        before = service.doc.build()[0]
        with patch('mmtr_editor.material_process.run_material_task') as worker:
            report = service.handle({'op': 'mmtr_validate_interfaces', 'dll': 'not-loaded.dll'})
        worker.assert_not_called()
        self.assertEqual((report['status'], report['scope'], report['reason']),
                         ('skipped', 'metadata_delta', 'no_metadata_edits'))
        self.assertIsNone(report['valid'])
        self.assertEqual((report['checked_shaders'], report['checked_records']), (0, 0))
        self.assertFalse(report['coverage']['associated_layout'])
        self.assertFalse(report['coverage']['material_markers'])
        self.assertEqual(service.doc.build()[0], before)
        with self.assertRaisesRegex(EditError, '范围'):
            service.handle({'op': 'mmtr_validate_interfaces', 'scope': 'everything'})

    def test_associated_validation_requires_known_materials(self):
        document = Document(mmtr_fixture(), Path('MaterialShader/other.mmtr.240906211'))
        with self.assertRaisesRegex(EditError, '同时打开'):
            validate_associated_layout(document, None, 'not-loaded.dll')
        with self.assertRaisesRegex(EditError, '没有使用'):
            validate_associated_layout(document, MdfDocument(mdf_fixture()), 'not-loaded.dll')


@unittest.skipUnless(DXC.is_file(), '本地原生 DXC 不可用')
class AssociatedLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 标记位于 16B 参数和 1 个纹理槽之后的 32B，分量值来自 MDF 的 float4。
        named = NAMED.replace('    uint4 slots=',
            '    uint guard=BindlessBuffer[(asuint(VAR_Value.x)>>4)+2].x;\n'
            '    if (guard != 1028) return 0;\n    uint4 slots=')
        heap = HEAP.replace('    Texture2D<float4> texture=',
            '    if (material.words[2].x == 1028) return VAR_Value;\n'
            '    Texture2D<float4> texture=')
        cls.sources = ((named, 'ps_6_0'), (named, 'ps_6_6'), (heap, 'ps_6_6'))
        cls.blobs = [compile_hlsl(str(DXC), source, 'main', profile)[0]
                     for source, profile in cls.sources]
        mdf = bytearray(mdf_fixture())
        mdf.extend(bytes(12))
        struct.pack_into('<H', mdf, 280+20, 4)
        struct.pack_into('<H', mdf, 304+20, 4)
        cls.mdf_source = bytes(mdf)

    def service(self, blobs=None):
        service = Service()
        service.doc = Document(source_with(blobs or self.blobs), Path('MaterialShader/test.mmtr.240906211'))
        service.mdf = MdfDocument(self.mdf_source)
        return service

    def validate(self, service):
        before = service.doc.build()[0], service.mdf.build()[0]
        result = service.handle({'op': 'mmtr_validate_interfaces', 'scope': 'associated_layout', 'dll': str(DXC)})
        self.assertEqual((service.doc.build()[0], service.mdf.build()[0]), before)
        self.assertEqual(service.doc.metadata_edits, [])
        return result

    def test_reopened_no_delta_validates_three_native_paths_and_all_known_users(self):
        report = self.validate(self.service())
        self.assertEqual((report['status'], report['scope'], report['valid']), ('passed', 'associated_layout', True))
        self.assertEqual((report['checked_shaders'], report['checked_records'], report['checked_markers']), (3, 15, 3))
        self.assertEqual(report['checked_native_texture_reads'], 3)
        self.assertEqual(report['target_materials'], [0, 1])
        self.assertEqual(report['metadata_edits'], 0)
        self.assertTrue(report['coverage']['complete'])
        self.assertEqual(report['coverage']['external_users'], 'not_scanned')
        self.assertFalse(report['game_tested'])

    def test_stale_guard_after_shader_only_reopen_and_unknown_index_fail(self):
        for source, profile in self.sources:
            with self.subTest(profile=profile, source=source):
                stale = compile_hlsl(str(DXC), source.replace('1028', '1029'), 'main', profile)[0]
                report = self.validate(self.service([stale]))
                self.assertEqual((report['status'], report['failed_shaders']), ('failed', 1))
                self.assertIn('标记', report['issues'][0]['problem'])
        unknown = NAMED.replace('NonUniformResourceIndex(slots.x)', 'NonUniformResourceIndex(slots.x+1)')
        blob = compile_hlsl(str(DXC), unknown, 'main', 'ps_6_6')[0]
        report = self.validate(self.service([blob]))
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['coverage']['complete'])
        self.assertIn('索引', report['issues'][0]['problem'])

    def test_shared_payload_counts_each_shader_and_preflight_conflicts_do_not_load_dxc(self):
        report = self.validate(self.service([self.blobs[0], self.blobs[0]]))
        self.assertEqual((report['checked_shaders'], report['unique_payloads'], report['checked_markers']), (2, 1, 2))
        service = self.service()
        altered = bytearray(self.mdf_source)
        struct.pack_into('<H', altered, 304+20, 3)
        service.mdf = MdfDocument(altered)
        with patch('mmtr_editor.material_process.run_material_task') as worker:
            report = self.validate(service)
        worker.assert_not_called()
        self.assertEqual((report['status'], report['checked_shaders']), ('failed', 0))
        self.assertEqual(report['issues'][0]['material_index'], 1)

    def test_results_discarded_if_dxc_or_input_identity_changes(self):
        service = self.service()
        with patch('mmtr_editor.material_process.run_material_task', return_value={}), \
             patch('mmtr_editor.material_validation.dll_identity', side_effect=['before', 'after']):
            with self.assertRaisesRegex(EditError, '身份改变'):
                self.validate(service)
        original = service.doc.build()[0]
        def concurrent_change(*args, **kwargs):
            service.doc.rename(service.doc.current_info()['records'][0]['name_offset'], 'Other')
            return {}
        with patch('mmtr_editor.material_process.run_material_task', side_effect=concurrent_change):
            with self.assertRaisesRegex(EditError, '身份改变'):
                self.validate(service)
        self.assertNotEqual(service.doc.build()[0], original)


if __name__ == '__main__':
    unittest.main()
