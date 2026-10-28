"""在临时工作目录经真实stdio验证独立源码或冻结MCP；只用合成MDF，不接触游戏。"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import site
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
site.addsitedir(str(ROOT / '.build' / 'editor-deps'))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_mdf_editing import fixture
from mmtr_editor import __version__
from mmtr_editor.mcp_server import WorkbenchSession, create_server


# 每个编辑/导出操作经过协议，错误反例验证服务端整批回滚；临时文件归本进程所有。
async def verify(executable, dxc=None, *, require_native_hash=False):
    command = str(Path(executable).resolve()) if executable else sys.executable
    arguments = ['--mcp'] if executable else [str(ROOT / 'scripts' / 'run_mcp.py')]
    # 按当前源码契约核对完整工具清单，避免工具增加后固定数量误报或冻结包遗漏工具。
    state = WorkbenchSession()
    try:
        expected_tools = {tool.name for tool in await create_server(state).list_tools()}
    finally:
        state.close()
    with tempfile.TemporaryDirectory(prefix='mmtr-standalone-') as folder:
        work = Path(folder)
        source = work / 'source.mdf2.45'
        original = fixture()
        source.write_bytes(original)
        parameters = StdioServerParameters(command=command, args=arguments, cwd=str(work), env={'MMTR_DXC_PATH': ''})
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                initialized = await client.initialize()
                tools = await client.list_tools()
                assert initialized.server_info.version == __version__
                actual_tools = {tool.name for tool in tools.tools}
                assert len(actual_tools) == len(tools.tools), 'MCP 工具名称重复'
                assert actual_tools == expected_tools, (
                    f'MCP 工具清单不一致：缺少 {sorted(expected_tools - actual_tools)}；'
                    f'多出 {sorted(actual_tools - expected_tools)}')

                async def call(name, **args):
                    result = await client.call_tool('workbench_' + name, args)
                    assert not result.is_error, result.content
                    return result.structured_content

                status = await call('status')
                hash_backend = status.get('xxh64_backend')
                if require_native_hash:
                    assert hash_backend == 'native', f'MCP未加载原生xxHash：{hash_backend}'

                if dxc:
                    await call('configure', dxc_path=str(Path(dxc).resolve()))
                    compiled = await call('compile', source='float4 main(float4 p:SV_Position):SV_Target { return float4(0,0,0,1); }', entry='main', profile='ps_6_2')
                    assert compiled['size'] > 0
                    shader = work / 'synthetic.dxbc'
                    await call('export_shader', path=str(shader), format='compiled')
                    from mmtr_editor.model import validate_shader
                    assert validate_shader(shader.read_bytes())['stage'] == 'ps'
                await call('open', mdf_path=str(source), material_index=0)
                await call('edit_mdf', actions=[
                    {'action': 'add_property', 'material_index': 0, 'name': 'NewParam', 'values': [0, 0, 0, 0]},
                    {'action': 'add_texture', 'material_index': 0, 'name': 'NewTexture', 'path': 'systems/rendering/NullBlack.tex'}])
                await call('set_master', path='MaterialShader/Variation/test.mmtr', material_index=0)
                candidate = work / 'candidate.mdf2.45'
                before = await call('export', mdf_path=str(candidate))
                failed = await client.call_tool('workbench_edit_mdf', {'actions': [
                    {'action': 'set_master', 'material_index': 0, 'path': 'MaterialShader/Variation/changed.mmtr'},
                    {'action': 'delete_property', 'material_index': 0, 'name': 'Missing'}]})
                assert failed.is_error
                after = await call('export', mdf_path=str(work / 'rollback.mdf2.45'))
                assert before['files'][0]['sha256'] == after['files'][0]['sha256']
                await call('open', mdf_path=str(candidate), material_index=0)
                material = await call('inspect', kind='material', index=0)
                assert material['master'] == 'MaterialShader/Variation/test.mmtr'
                assert (await call('list', kind='properties', query='NewParam'))['total'] == 1
                assert (await call('list', kind='textures', query='NewTexture'))['total'] == 1
                other = await call('inspect', kind='material', index=1)
                assert other['master'] == 'MaterialShader/test.mmtr'
                assert source.read_bytes() == original
    return {'passed': True, 'tools': len(actual_tools), 'version': __version__, 'command': command,
            'xxh64_backend': hash_backend,
            'temporary_cwd': True, 'synthetic_input_only': True, 'game_tested': False, 'native_compile_checked': bool(dxc),
            'executable_sha256': hashlib.sha256(Path(executable).read_bytes()).hexdigest() if executable else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', help='冻结mmtr-core.exe；省略则连接本仓库源码')
    parser.add_argument('--dxc', help='可选dxcompiler.dll，另验证隔离进程的合成HLSL编译')
    parser.add_argument('--require-native-hash', action='store_true', help='要求实际加载原生xxHash，阻止漏包后回退')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = asyncio.run(verify(args.executable, args.dxc, require_native_hash=args.require_native_hash))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
