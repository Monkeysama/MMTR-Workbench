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
        self.root = Path(self.folder.name)
        self.source = self.root / '原始.mdf2.45'
        self.source.write_bytes(fixture())
        self.state = WorkbenchSession()
        self.server = create_server(self.state)
        await self.call('open', mdf_path=str(self.source))

    async def call(self, tool_name, **args):
        result = await self.server.call_tool('workbench_' + tool_name, args)
        return result.structured_content

    async def test_tool_schemas_and_annotations(self):
        tools = {t.name: t for t in await self.server.list_tools()}
        self.assertEqual(len(tools), 20)
        self.assertTrue(tools['workbench_list'].annotations.read_only_hint)
        self.assertFalse(tools['workbench_export'].annotations.read_only_hint)
        self.assertTrue(tools['workbench_reset'].annotations.destructive_hint)
        self.assertTrue(tools['workbench_export'].annotations.open_world_hint)
        self.assertTrue(all(t.output_schema for t in tools.values()))
        schema = tools['workbench_edit_mdf'].input_schema
        self.assertIn('discriminator', schema['properties']['actions']['items'])
        self.assertEqual(tools['workbench_list'].input_schema['properties']['limit']['maximum'], 100)
        self.assertEqual(tools['workbench_redirect'].input_schema['properties']['field']['type'], 'integer')

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
