"""MCP工具契约和事务回归；合成MDF即可运行，不需要DXC或游戏语料。"""
import asyncio
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_mdf_editing import fixture
from test_structure import fixture as mmtr_fixture, PATH as MMTR_PATH

try:
    from mmtr_editor.mcp_server import WorkbenchSession, create_server
    from mcp.server.mcpserver.exceptions import ToolError
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


# SDK同步工具会运行在工作线程，测试仍通过公开MCPServer.call_tool验证类型及输出转换。
@unittest.skipUnless(SDK_AVAILABLE, 'MCP SDK未安装；运行npm run core:build或安装requirements-editor.txt')
class McpContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        # Windows runner的TEMP可能含8.3短路径；与导出预检统一使用解析后的文件身份。
        self.root = Path(self.folder.name).resolve()
        self.source = self.root / '原始.mdf2.45'
        self.source.write_bytes(fixture())
        self.state = WorkbenchSession()
        self.addCleanup(self.state.close)
        self.server = create_server(self.state)
        await self.call('open', mdf_path=str(self.source))

    async def call(self, tool_name, **args):
        result = await self.server.call_tool('workbench_' + tool_name, args)
        return result.structured_content

    async def test_tool_schemas_and_annotations(self):
        tools = {t.name: t for t in await self.server.list_tools()}
        self.assertEqual(len(tools), 34)
        self.assertTrue(tools['workbench_list'].annotations.read_only_hint)
        self.assertTrue(tools['workbench_usage'].annotations.read_only_hint)
        self.assertTrue(tools['workbench_removal_preview'].annotations.read_only_hint)
        self.assertFalse(tools['workbench_removal_submit'].annotations.read_only_hint)
        self.assertTrue(tools['workbench_removal_submit'].annotations.destructive_hint)
        self.assertTrue(tools['workbench_material_status'].annotations.read_only_hint)
        self.assertFalse(tools['workbench_material_submit'].annotations.read_only_hint)
        self.assertFalse(tools['workbench_material_cancel'].annotations.read_only_hint)
        self.assertTrue(tools['workbench_variant_export'].annotations.open_world_hint)
        self.assertFalse(tools['workbench_project_save'].annotations.read_only_hint)
        self.assertTrue(tools['workbench_project_read'].annotations.read_only_hint)
        self.assertIn('discriminator', tools['workbench_material_preview'].input_schema['properties']['actions']['items'])
        self.assertFalse(tools['workbench_export'].annotations.read_only_hint)
        self.assertTrue(tools['workbench_reset'].annotations.destructive_hint)
        self.assertTrue(tools['workbench_export'].annotations.open_world_hint)
        self.assertTrue(all(t.output_schema for t in tools.values()))
        template = await self.call('effect_template', name='emission_multiplier')
        self.assertEqual(template['actions'][0]['name'], 'MMTR_EmissionStrength')
        self.assertTrue(template['requires_compiled_shader'])
        self.assertTrue(tools['workbench_effect_preview'].annotations.read_only_hint)
        self.assertTrue(tools['workbench_validate'].annotations.read_only_hint)
        self.assertEqual(tools['workbench_validate'].input_schema['properties']['scope']['enum'],
                         ['metadata_delta', 'associated_layout'])
        schema = tools['workbench_edit_mdf'].input_schema
        self.assertIn('discriminator', schema['properties']['actions']['items'])
        self.assertEqual(tools['workbench_list'].input_schema['properties']['limit']['maximum'], 100)
        self.assertEqual(tools['workbench_redirect'].input_schema['properties']['field']['type'], 'integer')

    async def test_validation_scope_is_explicit_skips_without_delta_and_never_changes_revision(self):
        from mmtr_editor.model import Document
        self.state.service.doc = Document(mmtr_fixture(), MMTR_PATH)
        before = self.state.service.doc.build()[0], self.state.service.mdf.build()[0], self.state.revision
        report = await self.call('validate')
        self.assertEqual((report['status'], report['scope'], report['checked_shaders']),
                         ('skipped', 'metadata_delta', 0))
        self.assertIsNone(report['valid'])
        result = {'status': 'failed', 'scope': 'associated_layout', 'valid': False,
                  'checked_shaders': 1, 'issues': [{'kind': 'associated_layout', 'problem': '旧标记'}]}
        with patch('mmtr_editor.material_validation.validate_associated_layout', return_value=result) as validate:
            report = await self.call('validate', scope='associated_layout')
        self.assertEqual(report['scope'], 'associated_layout')
        self.assertFalse(report['valid'])
        validate.assert_called_once()
        self.assertEqual((self.state.service.doc.build()[0], self.state.service.mdf.build()[0], self.state.revision), before)
        with self.assertRaises(ToolError):
            await self.call('validate', scope='everything')

    async def test_project_tools_keep_source_and_revision_and_restore_only_intent(self):
        from mmtr_editor.model import Document
        folder = self.root/'MaterialShader'
        folder.mkdir()
        source = folder/'test.mmtr.240906211'
        source.write_bytes(mmtr_fixture())
        self.state.service.doc = Document(source.read_bytes(), source)
        self.state.service.mdf_selected = 0
        dll = self.root/'identity.dll'
        dll.write_bytes(b'not loaded; identity only')
        self.state.dll = str(dll)
        actions = [{'action': 'parameter', 'name': 'IntentP', 'byte_size': 4}]
        variant = dict(master='MaterialShader/independent.mmtr', mmtr_path=str(folder/'independent.mmtr.240906211'),
                       mdf_path=str(self.root/'independent.mdf2.45'))
        revision = self.state.revision
        path = str(self.root/'intent.mmtrproj.json')
        await self.call('project_save', path=path, actions=actions, record_index=0, variant=variant)
        read = await self.call('project_read', path=path)
        self.assertEqual(read['changed_inputs'], [])
        self.assertTrue(read['requires_replan'])
        self.assertFalse(read['can_execute_saved_plan'])
        self.assertEqual(self.state.revision, revision)
        self.assertFalse((folder/'independent.mmtr.240906211').exists())
        preview = await self.call('material_preview', actions=read['project']['request']['actions'],
            record_index=0, variant=read['project']['request']['variant'])
        self.assertEqual(preview['scope'], 'independent_variant')
        self.assertEqual(preview['target_materials'], [0])
        self.assertEqual(len(preview['known_users']), 2)
        with self.assertRaises(ToolError):
            await self.call('variant_export', job_id='missing')

    async def test_material_jobs_short_calls_and_error_leave_revision_unchanged(self):
        from threading import Event
        from mmtr_editor.model import Document
        dll = self.root/'job.dll'
        dll.write_bytes(b'not loaded; identity only')
        self.state.dll = str(dll)
        self.state.service.doc = Document(mmtr_fixture(), Path('MaterialShader/test.mmtr.240906211'))
        self.state.service.mdf_selected = 0
        actions = [{'action': 'parameter', 'name': 'AsyncP', 'byte_size': 4},
                   {'action': 'texture', 'name': 'AsyncMap'}]
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        revision = self.state.revision

        def compute(*args, worker_control, **kwargs):
            worker_control.progress(stage='shaders', completed=1, total=5)
            entered.set()
            if not release.wait(3):
                raise RuntimeError('test gate timed out')
            worker_control.check()
            raise RuntimeError('injected native rejection')

        plan = await self.call('material_preview', actions=actions, record_index=0)
        with patch('mmtr_editor.material_jobs.apply_material_batch', side_effect=compute):
            job = await self.call('material_submit', actions=actions, record_index=0,
                                  expected_plan=plan['digest'], request_key='sdk-retry')
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            status = await self.call('material_status', job_id=job['job_id'])
            self.assertEqual((status['stage'], status['completed']), ('shaders', 1))
            duplicate = await self.call('material_submit', actions=actions, record_index=0,
                                        expected_plan=plan['digest'], request_key='sdk-retry')
            self.assertEqual(job['job_id'], duplicate['job_id'])
            with self.assertRaises(ToolError):
                await self.call('material_result', job_id=job['job_id'])
            self.assertEqual(self.state.revision, revision)
            await self.call('material_cancel', job_id=job['job_id'])
            release.set()
            await asyncio.to_thread(self.state.close)
            status = await self.call('material_status', job_id=job['job_id'])
            self.assertEqual(status['state'], 'cancelled')
            self.assertEqual(self.state.revision, revision)

    # 读取统计只读：缺 MMTR 时拒绝；同一字节状态复用缓存，筛选与分页在缓存结果上进行。
    async def test_usage_requires_mmtr_caches_and_filters(self):
        with self.assertRaisesRegex(ToolError, '打开 MMTR'):
            await self.call('usage')
        report = {'material': 'Material0', 'shaders_analyzed': 3, 'complete': {'parameters': True, 'textures': True},
                  'parameters': [{'name': 'A', 'state': 'used'}, {'name': 'B', 'state': 'unused'}],
                  'textures': [{'name': 'T', 'state': 'descriptor'}], 'gpbf': [], 'failures': [], 'notes': []}
        doc = SimpleNamespace(build=lambda: (b'mmtr', {}))
        dll = self.root/'identity.dll'
        dll.write_bytes(b'not loaded; identity only')
        self.state.dll = str(dll)
        calls = []
        with patch.object(self.state, 'material_index', return_value=0), \
                patch.object(self.state, 'require_doc', return_value=doc), \
                patch.object(self.state, 'call', side_effect=lambda op, **args: calls.append(op) or report):
            result = await self.call('usage', state_filter='unused')
            self.assertEqual([row['name'] for row in result['items']], ['B'])
            self.assertEqual(result['counts']['textures']['descriptor'], 1)
            result = await self.call('usage', kind='textures', limit=1)
            self.assertEqual((result['total'], result['next_offset']), (1, None))
            self.assertFalse(result['coverage']['deletion_supported'])
            dll.write_bytes(b'replaced at same path')
            await self.call('usage')
        self.assertEqual(calls, ['mmtr_material_usage', 'mmtr_material_usage'])

    async def test_removal_preview_uses_background_owner_and_preserves_revision(self):
        from threading import Event
        from mmtr_editor.model import Document
        from mmtr_editor.material_removal_service import prepare_removal_preview
        self.state.service.doc = Document(mmtr_fixture(), Path('MaterialShader/test.mmtr.240906211'))
        self.state.service.mdf_selected = 0
        dll = self.root/'removal.dll'
        dll.write_bytes(b'identity only')
        self.state.dll = str(dll)
        revision = self.state.revision
        before = self.state.service.doc.build()[0], self.state.service.mdf.build()[0]
        entered, release = Event(), Event()
        self.addCleanup(release.set)

        def analyze(service, dll, actions, index, source_revision, *, worker_control):
            plan, _ = prepare_removal_preview(service, dll, actions, index, source_revision)
            entered.set()
            release.wait(3)
            worker_control.check()
            return {'plan': plan, 'eligible': False, 'shaders': [dict(shader_index=0, eligible=False)],
                    'failures': [dict(error='used texture')], 'coverage': {'deletion_supported': False}}

        args = dict(actions=[dict(action='texture', name='BaseMap')], request_key='remove-test')
        with patch('mmtr_editor.material_jobs.analyze_removal_preview', side_effect=analyze):
            job = await self.call('removal_preview', **args)
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            status = await self.call('material_status', job_id=job['job_id'])
            self.assertEqual(status['scope'], 'readonly_analysis')
            self.assertEqual((await self.call('status'))['revision'], revision)
            self.assertEqual((await self.call('removal_preview', **args))['job_id'], job['job_id'])
            release.set()
            for _ in range(200):
                status = await self.call('material_status', job_id=job['job_id'])
                if status['state'] in ('succeeded', 'failed', 'stale', 'cancelled'):
                    break
                await asyncio.sleep(.01)
            self.assertEqual(status['state'], 'succeeded', status)
            result = await self.call('material_result', job_id=job['job_id'], limit=1)
            self.assertFalse(result['result']['eligible'])
            self.assertFalse(result['result']['coverage']['deletion_supported'])
            self.assertEqual(self.state.revision, revision)
            self.assertEqual(before, (self.state.service.doc.build()[0], self.state.service.mdf.build()[0]))

    async def test_usage_rejects_dxc_changing_during_worker(self):
        dll = self.root/'changing.dll'
        dll.write_bytes(b'first')
        self.state.dll = str(dll)
        doc = SimpleNamespace(build=lambda: (b'mmtr', {}))

        def changed(*args, **kwargs):
            dll.write_bytes(b'second')
            return {}

        with patch.object(self.state, 'material_index', return_value=0), \
                patch.object(self.state, 'require_doc', return_value=doc), \
                patch.object(self.state, 'call', side_effect=changed):
            with self.assertRaisesRegex(ToolError, 'DXC .*改变'):
                await self.call('usage')
        self.assertIsNone(self.state.usage_cache)

    async def test_unicode_open_pagination_and_errors_preserve_session(self):
        result = await self.call('list', kind='materials', limit=1)
        self.assertEqual((result['total'], result['next_offset']), (2, 1))
        result = await self.call('list', kind='materials', offset=1, limit=1)
        self.assertEqual(result['items'][0]['name'], 'Material1')
        result = await self.call('list', kind='materials', query='MATERIAL0')
        self.assertEqual(result['total'], 1)
        with self.assertRaises(ToolError):
            await self.call('list', kind='shaders', limit=101)
        with self.assertRaises(ToolError):
            await self.call('inspect', kind='material', index=100)
        self.assertEqual((await self.call('status'))['mdf']['material_count'], 2)

    async def test_readonly_structure_queries_and_reverse_reference_paging(self):
        from mmtr_editor.model import Document
        self.state.service.doc = Document(mmtr_fixture(), MMTR_PATH)
        before = self.state.service.doc.build()[0]
        revision = self.state.revision
        report = await self.call('inspect', kind='structure', limit=1)
        self.assertEqual(report['program_count'], 4)
        self.assertEqual(report['preflight']['shader_analysis'], 'not_run')
        users = await self.call('inspect', kind='structure_shader_users', index=0, limit=1)
        self.assertEqual(users['total'], 4)
        self.assertEqual(users['next_offset'], 1)
        users = await self.call('inspect', kind='structure_shader_users', index=0, offset=3, limit=1)
        self.assertEqual(users['items'][0]['program'], 3)
        objects = await self.call('inspect', kind='structure_objects', limit=1)
        self.assertEqual(len(objects['items']), 1)
        obj = objects['items'][0]
        users = await self.call('inspect', kind='structure_object_users', index=obj['index'], limit=1)
        self.assertEqual(users['total'], 4)
        metadata = await self.call('inspect', kind='structure_metadata', limit=1)
        self.assertEqual(metadata['items'][0]['name'], 'UserMaterial')
        member = await self.call('inspect', kind='structure_metadata', index=0, limit=1)
        self.assertEqual(member['members']['items'][0]['name'], 'Value')
        program = await self.call('inspect', kind='structure_program', index=3)
        self.assertEqual(program['target'], 3)
        with self.assertRaises(ToolError):
            await self.call('inspect', kind='structure_program', index=100)
        self.assertEqual(self.state.revision, revision)
        self.assertEqual(self.state.service.doc.build()[0], before)

    async def test_structure_diagnostics_leave_old_browse_available(self):
        import struct
        from mmtr_editor.model import Document
        data = bytearray(mmtr_fixture())
        pointer = struct.unpack_from('<Q', data, 16+0x88)[0]
        data[pointer+6] = 128
        self.state.service.doc = Document(data, MMTR_PATH)
        self.assertEqual((await self.call('list', kind='passes'))['total'], 4)
        report = await self.call('inspect', kind='structure')
        self.assertEqual(report['preflight']['structure'], 'failed')
        diag = await self.call('inspect', kind='structure_diagnostics', limit=1)
        self.assertEqual(len(diag['items']), 1)
        self.assertIsNotNone(diag['next_offset'])
        self.assertEqual(self.state.service.doc.build()[0], bytes(data))

    async def test_batch_failure_restores_prior_changes_and_revision(self):
        await self.call('edit_mdf', actions=[{'action': 'set_property', 'material_index': 0,
                                            'name': 'Value', 'values': [0.25]}])
        before = self.state.service.mdf.build()[0]
        revision = self.state.revision
        with self.assertRaisesRegex(ToolError, '不存在'):
            await self.call('edit_mdf', actions=[
                {'action': 'set_property', 'material_index': 0, 'name': 'Value', 'values': [0.75]},
                {'action': 'delete_property', 'material_index': 1, 'name': 'Missing'}])
        self.assertEqual(self.state.service.mdf.build()[0], before)
        self.assertEqual(self.state.revision, revision)
        self.assertEqual(self.source.read_bytes(), fixture())

    async def test_batch_schema_rejects_unknown_fields_and_nonfinite_values(self):
        for extra in ({'values': [float('nan')]}, {'op': 'export'}):
            action = {'action': 'set_property', 'material_index': 0, 'name': 'Value', 'values': [1]}
            action.update(extra)
            with self.assertRaises(ToolError):
                await self.call('edit_mdf', actions=[action])
        self.assertEqual(self.state.service.mdf.build()[0], fixture())

    async def test_open_unsaved_guard_and_parse_failure(self):
        await self.call('edit_mdf', actions=[{'action': 'set_property', 'material_index': 0,
                                            'name': 'Value', 'values': [0.75]}])
        before = self.state.service.mdf.build()[0]
        with self.assertRaisesRegex(ToolError, '未导出'):
            await self.call('open', mdf_path=str(self.source))
        broken = self.root / 'broken.mmtr'
        broken.write_bytes(b'broken')
        with self.assertRaises(ToolError):
            await self.call('open', mmtr_path=str(broken), mdf_path=str(self.source), discard_unsaved=True)
        self.assertEqual(self.state.service.mdf.build()[0], before)
        await self.call('export', mdf_path=str(self.root / 'saved.mdf2.45'))
        self.assertFalse((await self.call('status'))['mdf']['unsaved'])
        await self.call('open', mdf_path=str(self.source))
        self.assertEqual(self.state.service.mdf.build()[0], fixture())

    async def test_export_protects_sources_hardlinks_and_duplicate_targets(self):
        alias = self.root / 'alias.mdf2.45'
        os.link(self.source, alias)
        for target in (self.source, alias):
            with self.assertRaisesRegex(ToolError, '原始 MDF'):
                await self.call('export', mdf_path=str(target))
        target = self.root / 'new.mdf2.45'
        with self.assertRaisesRegex(ToolError, '同一文件'):
            await self.call('export', mdf_path=str(target), report_path=str(target))
        self.assertFalse(target.exists())
        self.assertEqual(self.source.read_bytes(), fixture())

    async def test_export_prechecks_all_paths_before_any_write(self):
        target = self.root / 'good.mdf2.45'
        target.write_bytes(b'previous export')
        with self.assertRaisesRegex(ToolError, '目录不存在'):
            await self.call('export', mdf_path=str(target), report_path=str(self.root/'missing'/'report.json'))
        self.assertEqual(target.read_bytes(), b'previous export')

    async def test_export_io_failure_rolls_back_files_and_save_state(self):
        from mmtr_editor.model import atomic_write
        await self.call('edit_mdf', actions=[{'action': 'set_texture', 'material_index': 0,
                                            'slot': 0, 'path': 'systems/rendering/NullBlack.tex'}])
        target = self.root / 'existing.mdf2.45'
        report = self.root / 'report.json'
        target.write_bytes(b'previous export')
        saved = self.state.service.mdf.saved_sha256

        def write(path, data):
            if path == report:
                raise OSError('injected write failure')
            atomic_write(path, data)

        with patch('mmtr_editor.mcp_server.atomic_write', side_effect=write):
            with self.assertRaisesRegex(ToolError, 'injected'):
                await self.call('export', mdf_path=str(target), report_path=str(report))
        self.assertEqual(target.read_bytes(), b'previous export')
        self.assertFalse(report.exists())
        self.assertEqual(self.state.service.mdf.saved_sha256, saved)
        self.assertTrue((await self.call('status'))['mdf']['unsaved'])

    async def test_export_manifest_and_reset(self):
        await self.call('edit_mdf', actions=[{'action': 'set_setting', 'material_index': 0,
                                            'field': 'shader_lod', 'value': 8}])
        target = self.root / 'new.mdf2.45'
        result = await self.call('export', mdf_path=str(target), report_path=str(self.root/'报告.json'))
        self.assertEqual(result['files'][0]['sha256'], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertFalse((await self.call('status'))['mdf']['unsaved'])
        await self.call('reset', target='all')
        self.assertEqual(self.state.service.mdf.build()[0], fixture())
        self.assertTrue((await self.call('status'))['mdf']['unsaved'])

    async def test_native_compilation_failure_clears_stale_cache(self):
        self.state.service.compiled = b'old result'
        with patch('mmtr_editor.rpc.compile_hlsl', side_effect=ValueError('compiler failed')):
            with self.assertRaisesRegex(ToolError, 'compiler failed'):
                await self.call('compile', source='bad', entry='main', profile='ps_6_6')
        self.assertIsNone(self.state.service.compiled)
        self.assertEqual(self.state.service.mdf.build()[0], fixture())

    async def test_independent_sessions_and_config_failure(self):
        old = self.state.dll
        with self.assertRaises(ToolError):
            await self.call('configure', dxc_path=str(self.root/'missing.dll'))
        self.assertEqual(self.state.dll, old)
        other = create_server()
        status = (await other.call_tool('workbench_status', {})).structured_content
        self.assertIsNone(status['mdf'])

    async def test_master_tool_exports_reparsed_material_and_preserves_other(self):
        result = await self.call('set_master', path='MaterialShader/Variation/test.mmtr', material_index=0)
        self.assertEqual(result['old_master'], 'MaterialShader/test.mmtr')
        self.assertEqual(result['master'], 'MaterialShader/Variation/test.mmtr')
        self.assertFalse(result['mmtr_linked'])
        materials = (await self.call('list', kind='materials'))['items']
        self.assertEqual(materials[1]['master'], 'MaterialShader/test.mmtr')
        from mmtr_editor.mdf import MdfDocument
        target = self.root/'master.mdf2.45'
        await self.call('export', mdf_path=str(target))
        self.assertEqual(MdfDocument.open(target).material(0)['master'], result['master'])
        self.assertEqual(self.source.read_bytes(), fixture())

    async def test_master_batch_rollback_and_invalid_path_preserve_state(self):
        with self.assertRaises(ToolError):
            await self.call('edit_mdf', actions=[
                {'action': 'set_master', 'material_index': 0, 'path': 'MaterialShader/Variation/test.mmtr'},
                {'action': 'delete_property', 'material_index': 0, 'name': 'Missing'}])
        self.assertEqual(self.state.service.mdf.build()[0], fixture())
        with self.assertRaises(ToolError):
            await self.call('set_master', path='MaterialShader/../test.mmtr')
        self.assertEqual(self.state.service.mdf.build()[0], fixture())
        await self.call('edit_mdf', actions=[
            {'action': 'set_master', 'material_index': 1, 'path': 'MaterialShader/Variation/batch.mmtr'}])
        self.assertEqual((await self.call('inspect', kind='material', index=1))['master'],
                         'MaterialShader/Variation/batch.mmtr')

    async def test_parameter_mdf_failure_restores_mmtr_and_mdf_together(self):
        # 合成MMTR只验证工具层事务所有权；实际DXIL扩展另由真实stdio语料验收。
        self.state.service.doc = SimpleNamespace(source=self.root/'MaterialShader'/'test.mmtr',
            metadata_edits=[], current_info=lambda: {'metadata': {'cbuffers': [
                {'name': 'UserMaterial', 'buffer_size': 960}]}})

        def call(op, **kwargs):
            if op == 'mmtr_add_cbuffer_member':
                self.assertEqual(kwargs['byte_offset'], 960)
                self.state.service.doc.metadata_edits.append({'kind': 'extension'})
                return {'interface_sync': {'validation': {'valid': True}}}
            self.state.service.mdf.set_property(0, 'Value', [0.9])
            raise ValueError('MDF append failed')

        with patch.object(self.state, 'call', side_effect=call):
            with self.assertRaisesRegex(ToolError, 'MDF append failed'):
                await self.call('add_parameter', name='Example', byte_size=16)
        self.assertEqual(self.state.service.doc.metadata_edits, [])
        self.assertEqual(self.state.service.mdf.build()[0], fixture())

    async def test_unlinked_mdf_interface_add_rejected_before_mutation(self):
        self.state.service.doc = SimpleNamespace(source=self.root/'Unrelated.mmtr')
        with self.assertRaisesRegex(ToolError, '未关联'):
            await self.call('edit_mdf', actions=[{'action': 'add_property', 'material_index': 0,
                                                'name': 'Example', 'values': [0]}])
        with self.assertRaisesRegex(ToolError, '未关联'):
            await self.call('add_texture', name='Example', record_index=0)
        self.assertEqual(self.state.service.mdf.build()[0], fixture())


if __name__ == '__main__':
    unittest.main()
