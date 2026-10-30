"""官方SDK客户端的真实stdio验收；输入只读，所有候选写到显式指定的本地输出目录。"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import site
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
site.addsitedir(str(ROOT / '.build' / 'editor-deps'))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mmtr_editor import __version__
from mmtr_editor.mcp_server import WorkbenchSession, create_server


# 每个调用经过实际子进程和协议schema；不通过直接调用Service冒充MCP链路。
async def verify(args):
    state = WorkbenchSession()
    try:
        expected_tools = {tool.name for tool in await create_server(state).list_tools()}
    finally:
        state.close()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_paths = [Path(args.mmtr).resolve(), Path(args.mdf).resolve()]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths]
    command = str(Path(args.executable).resolve()) if args.executable else sys.executable
    arguments = ['--mcp'] if args.executable else [str(ROOT / 'scripts' / 'run_mcp.py')]
    params = StdioServerParameters(command=command, args=arguments)
    calls = []
    with (output / 'server-stderr.log').open('w', encoding='utf-8') as errors:
        async with stdio_client(params, errlog=errors) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=360) as session:
                initialized = await session.initialize()
                assert initialized.server_info.version == __version__, initialized
                tools = await session.list_tools()
                actual_tools = {tool.name for tool in tools.tools}
                assert len(actual_tools) == len(tools.tools), 'MCP 工具名称重复'
                assert actual_tools == expected_tools, (actual_tools, expected_tools)
                assert all(tool.output_schema for tool in tools.tools)

                async def call(tool_name, error=False, **kwargs):
                    result = await session.call_tool('workbench_' + tool_name, kwargs, read_timeout_seconds=360)
                    assert bool(result.is_error) == error, result
                    calls.append({'tool': tool_name, 'expected_error': error})
                    print('PASS', tool_name, '(expected error)' if error else '', flush=True)
                    return result.structured_content

                assert (await call('status'))['mmtr'] is None
                await call('open', mmtr_path=str(output/'missing.mmtr'), error=True)
                await call('list', kind='shaders', limit=101, error=True)
                await call('configure', dxc_path=args.dxc)
                status = await call('open', mmtr_path=str(source_paths[0]), mdf_path=str(source_paths[1]),
                                    material_index=args.material_index)
                assert status['mmtr']['shader_count'] > 0
                material = await call('inspect', kind='material', index=args.material_index)
                result = await call('list', kind='materials', query=material['name'])
                assert any(m['index'] == args.material_index for m in result['items'])
                result = await call('list', kind='shaders', limit=2)
                assert len(result['items']) == 2 and result['next_offset'] == 2
                assert len(json.dumps(result)) < 6000
                record = await call('inspect', kind='pass', index=args.record_index, limit=2)
                assert record['metadata_tables'] and record['refs']
                ref = record['refs'][0]
                await call('redirect', record=args.record_index, field=ref['field'], target=ref['shader'])
                result = await call('list', kind='metadata', query='UserMaterial', limit=3)
                assert len(result['items']) <= 3 and result['total'] > 3
                row = result['items'][0]
                await call('rename', pointer=row['pointer'], name=row['name'])
                result = await call('read_shader', index=args.shader_index, limit=3)
                assert result['total_lines'] > 3 and result['next_line'] == 3
                result = await call('read_shader', index=args.shader_index, format='hlsl', limit=3)
                assert result['entry'] and result['profile']
                hlsl = output/'重建.hlsl'
                await call('export_shader', index=args.shader_index, format='hlsl', path=str(hlsl))
                await call('compile', source_path=str(hlsl), entry=result['entry'], profile=result['profile'],
                           apply_index=args.shader_index)
                await call('export_shader', format='compiled', path=str(output/'compiled.dxbc'))
                await call('restore_shader', index=args.shader_index)
                await call('compile', source='invalid hlsl', entry='main', profile='ps_6_6', error=True)
                assert (await call('status'))['compiled_size'] == 0
                # 第一项成功、第二项失败时，整个MDF批次必须恢复，原有字段保持不变。
                prop = (await call('list', kind='properties', limit=1))['items'][0]
                await call('edit_mdf', actions=[
                    {'action': 'set_property', 'material_index': args.material_index,
                     'name': prop['name'], 'values': [0.125]*len(prop['values'])},
                    {'action': 'delete_property', 'material_index': args.material_index, 'name': '__missing__'}], error=True)
                assert (await call('list', kind='properties', limit=1))['items'][0]['values'] == prop['values']
                result = await call('add_parameter', name='TestParam', byte_size=16)
                assert result['mdf_synced'] and result['interface_sync']['validation']['valid']
                result = await call('add_texture', name='TestTexture', record_index=args.record_index)
                assert result['interface_sync']['validation']['valid']
                await call('open', mmtr_path=str(source_paths[0]), error=True)
                assert (await call('validate'))['valid']
                # 先检查所有目标，不能先写MMTR再因MDF源保护失败而留下部分产物。
                partial = output/'must-not-exist.mmtr'
                await call('export', mmtr_path=str(partial), mdf_path=str(source_paths[1]), error=True)
                assert not partial.exists()
                # 保留输入资源的版本后缀，不能将其它游戏的候选伪装成荒野版本。
                mmtr_out, mdf_out = (output / ('candidate' + ''.join(path.suffixes)) for path in source_paths)
                result = await call('export', mmtr_path=str(mmtr_out), mdf_path=str(mdf_out),
                                    report_path=str(output/'export-report.json'))
                hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (mmtr_out, mdf_out)]
                assert [row['sha256'] for row in result['files'][:2]] == hashes
                for actual, expected in zip(hashes, (args.expected_mmtr, args.expected_mdf)):
                    if expected:
                        assert actual == expected, (actual, expected)
                status = await call('status')
                assert not status['mmtr']['unsaved'] and not status['mdf']['unsaved']
                await call('reset', target='all')
                assert (await call('list', kind='properties', query='TestParam'))['total'] == 0
                assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths] == before
                evidence = {'passed': True, 'version': initialized.server_info.version, 'tools': len(tools.tools),
                            'command': command, 'args': arguments, 'source_sha256': before,
                            'candidate_sha256': hashes, 'source_preserved': True,
                            'game_installed': False, 'game_tested': False, 'calls': calls}
                if args.executable:
                    evidence['core_sha256'] = hashlib.sha256(Path(command).read_bytes()).hexdigest()
                (output/'verification.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding='utf-8')
    print('PASS: real stdio MCP lifecycle, editing, compilation, validation and exports', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mmtr', required=True)
    parser.add_argument('--mdf', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--dxc', required=True)
    parser.add_argument('--material-index', type=int, required=True)
    parser.add_argument('--record-index', type=int, required=True)
    parser.add_argument('--shader-index', type=int, default=22)
    parser.add_argument('--executable')
    parser.add_argument('--expected-mmtr')
    parser.add_argument('--expected-mdf')
    asyncio.run(verify(parser.parse_args()))


if __name__ == '__main__':
    main()
