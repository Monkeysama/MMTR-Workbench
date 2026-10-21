"""P4 独立变体核心：只改一个关联材质，源状态保持，输出冲突和普通失败可追溯。"""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_material_batch as batch_tests
import test_material_jobs as job_tests
from mmtr_editor.material_variant import (prepare_material_variant, build_material_variant,
    export_material_variant, validate_variant_master)
from mmtr_editor.material_service import prepare_material_batch
from mmtr_editor.mdf import MdfDocument, _public_data
from mmtr_editor.model import Document, EditError, atomic_write

NATIVE_DXC = batch_tests.NATIVE_DXC


class VariantPlanningTests(unittest.TestCase):
    """合成坏输入只用于规划和拒绝检查，不冒充有效 Shader。"""
    def setUp(self):
        helper = job_tests.MaterialJobTests('test_success_read_committed_paging_and_retry_after_commit')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.service, self.dll = helper.owner.service, helper.dll
        self.folder = Path(helper.folder.name)
        (self.folder/'MaterialShader/Variation').mkdir(parents=True)
        self.mmtr = self.folder/'MaterialShader/Variation/Independent.mmtr.240906211'
        self.mdf = self.folder/'variant.mdf2.45'
        self.master = 'MaterialShader/Variation/Independent.mmtr'
        self.actions = helper.actions

    def plan(self, **extra):
        return prepare_material_variant(self.service, self.dll, self.actions, 0,
            str(self.mmtr), str(self.mdf), self.master, **extra)

    def test_scope_preview_lists_all_users_but_only_selects_one(self):
        before = self.service.doc.build()[0], self.service.mdf.build()[0]
        plan = self.plan()
        self.assertEqual(plan.edit.target_materials, (0,))
        self.assertEqual([m[0] for m in plan.known_users], [0, 1])
        self.assertEqual(plan.report()['external_users'], 'not_scanned')
        self.assertEqual(plan.edit.variant_master, self.master)
        shared = prepare_material_batch(self.service, self.dll, self.actions, 0)[0]
        self.assertEqual(shared.target_materials, (0, 1))
        self.assertNotEqual(shared.digest, plan.edit.digest)
        self.assertEqual(before, (self.service.doc.build()[0], self.service.mdf.build()[0]))

    def test_existing_master_and_unrelated_bad_layout_are_distinguished(self):
        self.service.mdf.add_texture(1, 'OtherMap', 'systems/rendering/NullBlack.tex')
        self.assertEqual(self.plan().edit.target_materials, (0,))
        with self.assertRaisesRegex(EditError, '布局不同'):
            prepare_material_batch(self.service, self.dll, self.actions, 0)
        with self.assertRaisesRegex(EditError, '已使用'):
            prepare_material_batch(self.service, self.dll, self.actions, 0,
                                   variant_master='MaterialShader/test.mmtr')

    def test_path_version_alias_protection_and_existing_targets(self):
        bad = ['', '../x.mmtr', 'C:/MaterialShader/x.mmtr', 'MaterialShader/../x.mmtr',
               'MaterialShader/x.mmtr.240906211', 'MaterialShader//x.mmtr']
        for master in bad:
            with self.subTest(master=master), self.assertRaises(EditError):
                validate_variant_master(master)
        for path in (self.folder/'Independent.mmtr.240906211',
                     self.mmtr.with_suffix('.999'), self.folder/'BadMaterialShader/Variation/Independent.mmtr.240906211'):
            with self.assertRaises(EditError):
                prepare_material_variant(self.service, self.dll, self.actions, 0,
                                         str(path), str(self.mdf), self.master)
        self.mmtr.write_bytes(b'existing')
        with self.assertRaisesRegex(EditError, '已存在'):
            self.plan()
        self.assertIsNotNone(self.plan(overwrite=True).mmtr.prior_sha256)
        # 指向真实源的硬链接即使明确允许覆盖也必须拒绝。
        self.mmtr.unlink()
        original = self.folder/'source.mmtr.240906211'
        original.write_bytes(self.service.doc.original)
        self.service.doc = Document(original.read_bytes(), original)
        self.mmtr.hardlink_to(original)
        with self.assertRaisesRegex(EditError, '原始 MMTR'):
            self.plan(overwrite=True)


@unittest.skipUnless(NATIVE_DXC.is_file(), '本机 DXC 缺失，独立变体原生验收跳过')
class NativeVariantTests(unittest.TestCase):
    """自有 HLSL 的隔离 DXC 实验，导出重开不影响调用方或未选材质。"""
    @classmethod
    def setUpClass(cls):
        batch_tests.NativeMaterialBatchTests.setUpClass()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.service = batch_tests.NativeMaterialBatchTests().service()
        self.service.mdf.source = self.root/'source.mdf2.45'
        self.service.mdf.source.write_bytes(self.service.mdf.original)
        (self.root/'MaterialShader').mkdir()
        self.mmtr = self.root/'MaterialShader/variant.mmtr.240906211'
        self.mdf = self.root/'variant.mdf2.45'
        self.master = 'MaterialShader/variant.mmtr'
        self.actions = [{'action': 'parameter', 'name': 'VariantP', 'byte_size': 8, 'values': [1.25, 2.5]},
                        {'action': 'texture', 'name': 'VariantMap'}]
        self.before = self.service.doc.build()[0], self.service.mdf.build()[0]

    def build(self, *, overwrite=False):
        plan = prepare_material_variant(self.service, NATIVE_DXC, self.actions, 0,
            self.mmtr, self.mdf, self.master, overwrite=overwrite)
        return build_material_variant(self.service, NATIVE_DXC, self.actions, 0, plan)

    def test_only_selected_material_changes_and_exports_reopen(self):
        candidate = self.build()
        self.assertEqual(self.before, (self.service.doc.build()[0], self.service.mdf.build()[0]))
        self.assertFalse(self.mmtr.exists())
        self.assertFalse(self.mdf.exists())
        report = export_material_variant(self.service, NATIVE_DXC, candidate)
        self.assertEqual(len(report['files']), 2)
        mdf = MdfDocument(self.mdf.read_bytes(), self.mdf)
        original = MdfDocument(self.before[1], self.service.mdf.source)
        self.assertEqual(_public_data(mdf.material(1)), _public_data(original.material(1)))
        self.assertEqual(mdf.material(0)['master'], self.master)
        self.assertEqual(mdf.material(0)['properties'][-1]['values'], [1.25, 2.5])
        self.assertEqual(mdf.material(0)['textures'][-1]['name'], 'VariantMap')
        self.assertEqual(mdf.material(0)['textures'][-1]['path'], 'systems/rendering/NullBlack.tex')
        doc = Document(self.mmtr.read_bytes(), self.mmtr)
        self.assertFalse(doc.integrity['shader_trailer_mismatches'])
        self.assertEqual(self.before, (self.service.doc.build()[0], self.service.mdf.build()[0]))

    def test_late_target_or_source_change_refuses_export(self):
        candidate = self.build()
        self.mdf.write_bytes(b'appeared after preview')
        with self.assertRaisesRegex(EditError, '目标在预览后改变'):
            export_material_variant(self.service, NATIVE_DXC, candidate)
        self.assertFalse(self.mmtr.exists())
        self.mdf.unlink()
        self.service.mdf.set_property(0, 'Value', [0.25]*4)
        with self.assertRaisesRegex(EditError, '必须重新规划'):
            export_material_variant(self.service, NATIVE_DXC, candidate)
        self.assertFalse(self.mmtr.exists())

    def test_write_failure_restores_both_existing_or_new_targets(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.mmtr.write_bytes(b'old MMTR')
                    self.mdf.write_bytes(b'old MDF')
                candidate = self.build(overwrite=existing)
                failed = [False]
                def injected(path, data):
                    if Path(path) == self.mdf and not failed[0]:
                        failed[0] = True
                        raise OSError('injected disk failure')
                    atomic_write(path, data)
                with patch('mmtr_editor.material_variant.atomic_write', side_effect=injected):
                    with self.assertRaisesRegex(OSError, 'injected disk failure'):
                        export_material_variant(self.service, NATIVE_DXC, candidate)
                if existing:
                    self.assertEqual(self.mmtr.read_bytes(), b'old MMTR')
                    self.assertEqual(self.mdf.read_bytes(), b'old MDF')
                else:
                    self.assertFalse(self.mmtr.exists())
                    self.assertFalse(self.mdf.exists())

    def test_candidate_tamper_and_worker_failure_preserve_source(self):
        candidate = self.build()
        with self.assertRaisesRegex(EditError, '候选载荷身份'):
            export_material_variant(self.service, NATIVE_DXC, replace(candidate, mmtr_bytes=b'bad'))
        with patch('mmtr_editor.material_service.extend_in_worker', side_effect=EditError('rejected')):
            with self.assertRaisesRegex(EditError, 'rejected'):
                self.build()
        self.assertEqual(self.before, (self.service.doc.build()[0], self.service.mdf.build()[0]))
        self.assertFalse(self.mmtr.exists())
