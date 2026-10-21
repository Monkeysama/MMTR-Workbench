"""P5完整删除候选：真实隔离DXC、MDF同步、回滚及保留字段身份；不修改游戏文件。"""
from pathlib import Path
import struct
import unittest
import tempfile
import time
from threading import RLock
from types import SimpleNamespace
from unittest.mock import patch

import test_material_batch as fixture
from mmtr_editor.dxc_process import compile_hlsl
from mmtr_editor.hashes import xxh64
from mmtr_editor.rpc import Service
from mmtr_editor.material_removal_service import apply_material_removal
from mmtr_editor.material_extension import current_layout
from mmtr_editor.mdf import MdfDocument, _public_data
from mmtr_editor.model import Document, EditError
from mmtr_editor.structure import document_structure


@unittest.skipUnless(fixture.NATIVE_DXC.is_file(), 'native DXC unavailable')
class NativeRemovalExecutionTests(unittest.TestCase):
    """自有HLSL具名/实例读取夹具由既有批次测试提供，所有原生操作在子进程中执行。"""
    @classmethod
    def setUpClass(cls):
        fixture.NativeMaterialBatchTests.setUpClass()

    def setUp(self):
        self.service = fixture.NativeMaterialBatchTests().service()

    def extend(self, actions):
        return fixture.NativeMaterialBatchTests().apply(self.service, actions)

    def remove(self, actions):
        return apply_material_removal(self.service, str(fixture.NATIVE_DXC), actions)

    def snapshot(self):
        return self.service.doc.build()[0], self.service.mdf.build()[0]

    def test_background_independent_delete_export_preserves_source_and_other_material(self):
        from mmtr_editor.material_jobs import MaterialJobs, TERMINAL
        self.extend([dict(action='parameter', name='Dead', byte_size=4), dict(action='texture', name='DeadMap')])
        before = self.snapshot()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root/'MaterialShader').mkdir()
            variant = dict(master='MaterialShader/removed.mmtr',
                mmtr_path=str(root/'MaterialShader/removed.mmtr.240906211'), mdf_path=str(root/'removed.mdf2.45'))
            owner = SimpleNamespace(service=self.service, lock=RLock(), revision=7, dll=str(fixture.NATIVE_DXC))
            jobs = MaterialJobs(owner)
            try:
                actions = [dict(action='parameter', name='Dead'), dict(action='texture', name='DeadMap')]
                def wait(job):
                    deadline = time.monotonic()+20
                    while time.monotonic() < deadline:
                        status = jobs.status(job['job_id'])
                        if status['state'] in TERMINAL:
                            self.assertEqual(status['state'], 'succeeded', status)
                            return status
                        time.sleep(.01)
                    self.fail('removal task timeout')
                preview = wait(jobs.removal_preview(actions, 0, 'preview', variant=variant))
                self.assertTrue(jobs.result(preview['job_id'])['result']['eligible'])
                submitted = jobs.removal_submit(actions, 0, preview['plan_digest'], 'execute', variant=variant)
                done = wait(submitted)
                self.assertTrue(done['candidate_available'])
                self.assertEqual(owner.revision, 7)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(jobs.removal_submit(actions, 0, preview['plan_digest'], 'execute', variant=variant)['job_id'], done['job_id'])
                exported = jobs.export_variant(done['job_id'])
                self.assertEqual(jobs.export_variant(done['job_id']), exported)
                mdf = MdfDocument(Path(variant['mdf_path']).read_bytes(), Path(variant['mdf_path']))
                self.assertEqual(mdf.material(0)['master'], variant['master'])
                self.assertEqual([p['name'] for p in mdf.material(0)['properties']], ['Value'])
                self.assertEqual(_public_data(mdf.material(1)), _public_data(self.service.mdf.material(1)))
                doc = Document(Path(variant['mmtr_path']).read_bytes(), Path(variant['mmtr_path']))
                self.assertEqual(current_layout(doc, mdf.material(0)).parameter_bytes, 16)
                self.assertEqual(self.snapshot(), before)
            finally:
                jobs.close()

    def test_tail_mixed_delete_reopens_all_known_materials_and_preserves_values_and_bindings(self):
        original = _public_data(self.service.mdf.materials)
        self.extend([dict(action='parameter', name='Dead', byte_size=4), dict(action='texture', name='DeadMap')])
        before_structure = document_structure(self.service.doc)
        report = self.remove([dict(action='parameter', name='Dead'), dict(action='texture', name='DeadMap')])
        self.assertTrue(report['interface_sync']['validation']['valid'])
        self.assertTrue(report['interface_sync']['coverage']['deletion_supported'])
        self.assertFalse(report['interface_sync']['game_tested'])
        self.assertEqual(report['target_materials'], [0, 1])
        doc = Document(self.service.doc.build()[0], self.service.doc.source)
        mdf = MdfDocument(self.service.mdf.build()[0], self.service.mdf.source)
        self.assertEqual(_public_data(mdf.materials), original)
        self.assertEqual(current_layout(doc, mdf.material(0)).parameter_bytes, 16)
        after_structure = document_structure(doc)
        self.assertFalse(after_structure.diagnostics)
        for before, after in zip(before_structure.programs, after_structure.programs):
            self.assertEqual(after.tables[2].bindings[0].raw, before.tables[2].bindings[0].raw)
            self.assertEqual(after.raw[0x100:], before.raw[0x100:])
        # 事务结束后继续追加与删除；不能因旧扩展尾部或验证缓存复活已删除接口。
        self.extend([dict(action='texture', name='NextMap')])
        self.remove([dict(action='texture', name='NextMap')])
        self.assertEqual(_public_data(self.service.mdf.materials), original)

    def test_middle_parameter_and_texture_deletion_preserves_remaining_offsets_registers_and_defaults(self):
        self.extend([dict(action='parameter', name='Dead', byte_size=4),
                     dict(action='parameter', name='Keep', byte_size=12, values=[1.25, 2.5, 3.75]),
                     dict(action='texture', name='DeadMap'), dict(action='texture', name='KeepMap')])
        before = document_structure(self.service.doc)
        report = self.remove([dict(action='parameter', name='Dead'), dict(action='texture', name='DeadMap')])
        self.assertEqual(report['interface_sync']['shaders_analyzed'], 1)
        for material in self.service.mdf.materials:
            self.assertEqual([(p['name'], p['offset']) for p in material['properties']], [('Value', 0), ('Keep', 16)])
            self.assertEqual(material['properties'][1]['values'], [1.25, 2.5, 3.75])
            self.assertEqual(material['property_block_size'], 32)
            self.assertEqual([t['name'] for t in material['textures']], ['BaseMap', 'KeepMap'])
        after = document_structure(self.service.doc)
        for old, new in zip(before.programs, after.programs):
            self.assertEqual([b.raw for b in new.tables[2].bindings], [old.tables[2].bindings[i].raw for i in (0, 2)])
            self.assertEqual(new.tables[2].expected_counts, (0, 0, 0, 0, 2, 0))
            self.assertEqual(new.total_stage_bindings, 3)

    def test_used_field_native_failure_stale_plan_and_dxc_change_preserve_both_documents(self):
        self.extend([dict(action='parameter', name='Dead', byte_size=4), dict(action='texture', name='DeadMap')])
        before = self.snapshot()
        for action in (dict(action='parameter', name='Value'), dict(action='texture', name='BaseMap')):
            with self.subTest(action=action), self.assertRaises(EditError):
                self.remove([action])
            self.assertEqual(self.snapshot(), before)
        actions = [dict(action='texture', name='DeadMap')]
        with self.assertRaisesRegex(EditError, '重新预览'):
            apply_material_removal(self.service, str(fixture.NATIVE_DXC), actions, expected_plan='0'*64)
        with patch('mmtr_editor.material_process.run_material_task', side_effect=EditError('native failed')):
            with self.assertRaisesRegex(EditError, 'native failed'):
                self.remove(actions)
        self.assertEqual(self.snapshot(), before)

    def test_unknown_source_binding_counts_and_unrepresented_members_fail_before_mutation(self):
        self.extend([dict(action='texture', name='DeadMap')])
        data = bytearray(self.service.doc.build()[0])
        data[16+0xE8] += 1
        self.service.doc = Document(data, self.service.doc.source)
        before = self.snapshot()
        with self.assertRaisesRegex(EditError, '结构预检'):
            self.remove([dict(action='texture', name='DeadMap')])
        self.assertEqual(self.snapshot(), before)

    def test_live_parameter_after_middle_field_moves_and_all_texture_bindings_can_be_removed(self):
        # 自有Shader读取第三个参数和后面的静态纹理；删除中间项必须迁移真正的活跃读取。
        self.extend([dict(action='parameter', name='Dead', byte_size=4),
                     dict(action='parameter', name='Keep', byte_size=16, values=[1, 2, 3, 4]),
                     dict(action='texture', name='DeadMap'), dict(action='texture', name='KeepMap')])
        blob = compile_hlsl(str(fixture.NATIVE_DXC), '''
cbuffer UserMaterial : register(b0) { float4 VAR_Value : packoffset(c0); float VAR_Dead : packoffset(c1); float4 VAR_Keep : packoffset(c2); };
Texture2D<float4> BaseMap : register(t3);
Texture2D<float4> DeadMap : register(t4);
Texture2D<float4> KeepMap : register(t5);
float4 main(float4 position : SV_Position) : SV_Target { return VAR_Value + VAR_Keep * KeepMap.Load(int3(position.xy,0)); }
''', 'main', 'ps_6_6')[0]
        for index in range(2):
            self.service.doc.replace_shader(index, blob, allow_interface_change=True)
        # 显式重开本次自有输入；旧扩展证明不能授权手动Shader替换后的导出。
        self.service.doc = Document(self.service.doc.build()[0], self.service.doc.source)
        result = self.remove([dict(action='parameter', name='Dead'), dict(action='texture', name='DeadMap')])
        self.assertTrue(result['interface_sync']['validation']['valid'])
        self.assertEqual(self.service.mdf.material(0)['properties'][1]['offset'], 16)
        self.assertEqual(self.service.mdf.material(0)['properties'][1]['values'], [1, 2, 3, 4])
        for program in document_structure(self.service.doc).programs:
            self.assertEqual([b.slots[4] for b in program.tables[2].bindings], [3, 5])

        # 最后一张无消费者纹理可以归零；绑定双指针/资源根分类同时归零。
        self.setUp()
        blob = compile_hlsl(str(fixture.NATIVE_DXC), '''
cbuffer UserMaterial : register(b0) { float4 VAR_Value; };
float4 main(float4 position: SV_Position) : SV_Target { return VAR_Value; }
''', 'main', 'ps_6_6')[0]
        for index in range(2):
            self.service.doc.replace_shader(index, blob, allow_interface_change=True)
        self.service.doc = Document(self.service.doc.build()[0], self.service.doc.source)
        self.remove([dict(action='texture', name='BaseMap')])
        for program in document_structure(self.service.doc).programs:
            self.assertEqual(program.tables[2].count, 0)
            self.assertEqual(program.tables[2].expected_counts, (0,)*6)
        self.assertEqual(self.service.mdf.material(0)['textures'], [])


if __name__ == '__main__':
    unittest.main()
