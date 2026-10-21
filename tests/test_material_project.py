"""轻量工程只恢复意图；旧偏移、外部代码、错配输入和未保存载荷不能伪装为可执行工程。"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from threading import RLock
from types import SimpleNamespace
import unittest

import test_material_jobs as job_tests
from mmtr_editor.material_jobs import MaterialJobs
from mmtr_editor.material_project import create_project, save_project, read_project, validate_project
from mmtr_editor.mdf import MdfDocument
from mmtr_editor.model import Document, EditError


class MaterialProjectTests(unittest.TestCase):
    def setUp(self):
        helper = job_tests.MaterialJobTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.root = Path(helper.folder.name).resolve()
        self.owner = helper.owner
        service = self.owner.service
        (self.root/'MaterialShader').mkdir()
        source = self.root/'MaterialShader/test.mmtr.240906211'
        source.write_bytes(service.doc.build()[0])
        service.doc = Document(source.read_bytes(), source)
        mdf = self.root/'source.mdf2.45'
        mdf.write_bytes(service.mdf.build()[0])
        service.mdf = MdfDocument(mdf.read_bytes(), mdf)
        self.actions = [{'action': 'parameter', 'name': 'ProjectP', 'byte_size': 8, 'values': [.25, 2.]},
                        {'action': 'texture', 'name': 'ProjectMap'}]
        self.owner.jobs = MaterialJobs(self.owner)
        self.addCleanup(self.owner.jobs.close)
        self.path = self.root/'intent.mmtrproj.json'
        self.variant = dict(master='MaterialShader/variant.mmtr',
                            mmtr_path=str(self.root/'MaterialShader/variant.mmtr.240906211'),
                            mdf_path=str(self.root/'variant.mdf2.45'))

    def save(self, **extra):
        return save_project(self.owner, self.path, self.actions, 0, **extra)

    def test_roundtrip_restores_actions_without_offsets_or_mutating_source(self):
        before = self.owner.service.doc.build()[0], self.owner.service.mdf.build()[0], self.owner.revision
        result = self.save(variant=self.variant)
        read = read_project(self.owner, self.path)
        self.assertEqual(read['changed_inputs'], [])
        self.assertTrue(read['requires_replan'])
        self.assertFalse(read['can_execute_saved_plan'])
        request = read['project']['request']
        self.assertEqual(request['actions'][0]['values'], [.25, 2.])
        self.assertEqual(request['variant']['master'], self.variant['master'])
        self.assertNotIn('byte_offset', request['actions'][0])
        self.assertNotIn('plan', read['project'])
        self.assertLess(result['size'], 5000)
        self.assertEqual(before, (self.owner.service.doc.build()[0], self.owner.service.mdf.build()[0], self.owner.revision))
        original = self.owner.jobs.preview(self.actions, 0, variant=self.variant)
        fresh = self.owner.jobs.preview(request['actions'], request['record_index'], variant=request['variant'])
        self.assertEqual(original['new'], fresh['new'])
        self.assertEqual(original['digest'], fresh['digest'])

    def test_source_content_changes_require_replan_but_identity_mismatch_rejects(self):
        self.save()
        self.owner.service.mdf.set_property(0, 'Value', [.125])
        self.owner.dll = str(self.root/'other.dll')
        Path(self.owner.dll).write_bytes(b'another identity; not loaded')
        read = read_project(self.owner, self.path)
        self.assertEqual(read['changed_inputs'], ['mdf', 'dxc'])
        self.assertTrue(read['requires_replan'])
        self.owner.service.mdf_selected = 1
        with self.assertRaisesRegex(EditError, '材质身份'):
            read_project(self.owner, self.path)

    def test_unsaved_inputs_output_conflicts_and_hardlinks_reject(self):
        self.owner.service.mdf.set_property(0, 'Value', [.1])
        with self.assertRaisesRegex(EditError, '未另存'):
            self.save()
        mdf = self.owner.service.mdf
        mdf.reset()
        self.save()
        with self.assertRaisesRegex(EditError, '已存在'):
            self.save()
        self.save(overwrite=True)
        self.path.unlink()
        self.path.hardlink_to(mdf.source)
        with self.assertRaisesRegex(EditError, '原始 MDF'):
            self.save(overwrite=True)

    def test_schema_corruption_nonfinite_unknown_fields_and_duplicate_json_reject(self):
        value = create_project(self.owner, self.actions, 0)
        mutations = [
            lambda x: x.update(schema=2), lambda x: x.update(schema=True),
            lambda x: x.update(command='do not execute'),
            lambda x: x['sources']['mdf'].update(sha256='bad'),
            lambda x: x['request']['actions'][0].update(byte_offset=16),
            lambda x: x['request']['actions'][0].update(values=[float('nan'), 0]),
            lambda x: x['request']['actions'][0].update(values=[float('inf'), 0]),
            lambda x: x['validation'].update(game_tested=True),
        ]
        for mutate in mutations:
            broken = deepcopy(value)
            mutate(broken)
            with self.subTest(value=broken), self.assertRaises(EditError):
                validate_project(broken)
        self.path.write_text('{"schema":1,"schema":1}', encoding='utf-8')
        with self.assertRaisesRegex(EditError, '重复'):
            read_project(self.owner, self.path)
        self.path.write_bytes(b'not json')
        with self.assertRaisesRegex(EditError, 'UTF-8 JSON'):
            read_project(self.owner, self.path)
