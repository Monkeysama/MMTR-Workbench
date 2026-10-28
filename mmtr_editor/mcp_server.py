"""Workbench 的标准 stdio MCP 入口；每个进程拥有独立文档，stdout 仅用于协议。"""
from copy import deepcopy
from functools import wraps
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import sys
from threading import RLock
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from . import __version__
from .hashes import XXH64_BACKEND
from .mdf import MdfDocument, _public_data, master_matches
from .model import Document, EditError, atomic_write
from .rpc import Service
from .bounded_cache import ByteLRU

Index = Annotated[int, Field(ge=0)]
Limit = Annotated[int, Field(ge=1, le=100)]
Name = Annotated[str, Field(min_length=1, max_length=256)]
Values = Annotated[list[FiniteFloat], Field(min_length=1, max_length=64)]


# 批量动作使用封闭的可验证类型；不暴露任意 RPC，也不接受执行代码或命令。
class MdfAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    material_index: Index


class PropertyAction(MdfAction):
    action: Literal['set_property', 'add_property']
    name: Name
    values: Values


class ComponentsAction(MdfAction):
    action: Literal['set_property_components']
    name: Name
    components: dict[str, FiniteFloat]


class DeletePropertyAction(MdfAction):
    action: Literal['delete_property']
    name: Name


class TextureAction(MdfAction):
    action: Literal['set_texture']
    slot: Index
    path: Name


class AddTextureAction(MdfAction):
    action: Literal['add_texture']
    name: Name
    path: Name


class MasterAction(MdfAction):
    action: Literal['set_master']
    path: Annotated[str, Field(min_length=1, max_length=32767)]


class DeleteTextureAction(MdfAction):
    action: Literal['delete_texture']
    slot: Index


class SettingAction(MdfAction):
    action: Literal['set_setting']
    field: Literal['shader_type', 'shader_lod', 'bake_texture_array_size']
    value: Annotated[int, Field(ge=-0x80000000, le=0x7fffffff)]


class FlagsAction(MdfAction):
    action: Literal['set_flags']
    flags_a: Annotated[int, Field(ge=0, le=0xffffffff)] | None = None
    flags_b: Annotated[int, Field(ge=0, le=0xffffffff)] | None = None


Action = Annotated[PropertyAction | ComponentsAction | DeletePropertyAction | TextureAction |
                   AddTextureAction | DeleteTextureAction | SettingAction | FlagsAction | MasterAction,
                   Field(discriminator='action')]


class ParameterAddition(BaseModel):
    """批量追加支持 float1..4；默认值数量由共享计划核心再次校验。"""
    model_config = ConfigDict(extra='forbid')
    action: Literal['parameter']
    name: Name
    byte_size: Literal[4, 8, 12, 16]
    values: Annotated[list[FiniteFloat], Field(min_length=1, max_length=4)] | None = None


class TextureAddition(BaseModel):
    """Texture2D 无消费者槽；默认使用系统 NullBlack。"""
    model_config = ConfigDict(extra='forbid')
    action: Literal['texture']
    name: Name
    path: Annotated[str, Field(min_length=1, max_length=32767)] = 'systems/rendering/NullBlack.tex'


MaterialAction = Annotated[ParameterAddition | TextureAddition, Field(discriminator='action')]
MaterialActions = Annotated[list[MaterialAction], Field(min_length=1, max_length=100)]


class VariantOptions(BaseModel):
    """独立候选的显式输出范围；计算不写文件，导出仍需另一个工具调用。"""
    model_config = ConfigDict(extra='forbid')
    master: Name
    mmtr_path: str
    mdf_path: str
    overwrite: bool = False


class RemovalAction(BaseModel):
    """P5 有界删除目标；消费者证明与完整候选验证分别执行。"""
    model_config = ConfigDict(extra='forbid')
    action: Literal['parameter', 'texture']
    name: Name


RemovalActions = Annotated[list[RemovalAction], Field(min_length=1, max_length=100)]


# 所有 SDK 工作线程串行访问同一 Service；锁不交给 deepcopy，事务失败恢复整个文档状态。
class WorkbenchSession:
    def __init__(self):
        self.service = Service()
        self.lock = RLock()
        self.dll = self.service.handle({'op': 'ping'})['dxc']
        self.revision = 0
        self.saved_mmtr = None
        self.text_cache = ByteLRU(8, 16 * 1024 * 1024)
        self.snapshot_cache = None
        # 读取统计按 MMTR/MDF/DXC 当前内容缓存一份，不能把原路径被替换的 DLL 当成同一个输入。
        self.usage_cache = None
        from .material_jobs import MaterialJobs
        self.jobs = MaterialJobs(self)

    def invalidate(self):
        self.snapshot_cache = None
        # 文本按实际载荷／格式／DLL 内容寻址；MDF 路径与未应用编译不改变键。
        self.usage_cache = None

    def close(self):
        self.jobs.close()

    def require_doc(self):
        if self.service.doc is None:
            raise EditError('请先用 workbench_open 打开 MMTR。')
        return self.service.doc

    def material_index(self, index=None, linked=False):
        service = self.service
        if service.mdf is None:
            raise EditError('请先用 workbench_open 打开 MDF。')
        index = service.mdf_selected if index is None else index
        if index is None:
            raise EditError('请指定 material_index。')
        material = service.mdf.material(index)
        if linked and not master_matches(material['master'], str(self.require_doc().source)):
            raise EditError('所选 MDF 材质未关联当前 MMTR。')
        return index

    def snapshot(self):
        self.require_doc()
        if self.snapshot_cache is None:
            self.snapshot_cache = self.service.snapshot()
        return self.snapshot_cache

    def status(self):
        service = self.service
        result = {'version': __version__, 'revision': self.revision, 'dxc_path': self.dll,
                  'xxh64_backend': XXH64_BACKEND,
                  'session': 'independent_process', 'transport': 'stdio',
                  'capabilities': ['paged_queries', 'shader_compile_replace', 'mdf_batch_edit',
                                   'auto_parameter_texture_extension', 'mdf_master_edit', 'validated_export',
                                   'readonly_structure', 'material_input_usage', 'material_batch_jobs',
                                   'independent_material_variant', 'material_project'],
                  'mmtr': None, 'mdf': None, 'compiled_size': len(service.compiled or b'')}
        if service.doc:
            doc = service.doc
            result['mmtr'] = {'source': str(doc.source), 'dirty': doc.dirty,
                              'unsaved': hashlib.sha256(doc.build()[0]).hexdigest() != self.saved_mmtr,
                              'shader_count': len(doc.info['shaders']), 'pass_count': len(doc.info['records']),
                              'changes': {'shaders': len(doc.replacements), 'names': len(doc.renames),
                                          'references': len(doc.redirects), 'metadata': len(doc.metadata_edits)}}
        if service.mdf:
            mdf = service.mdf
            result['mdf'] = {'source': str(mdf.source), 'selected_material': service.mdf_selected,
                             'material_count': len(mdf.materials), 'changes': len(mdf.edit_notes),
                             'unsaved': mdf.build()[1]['sha256'] != mdf.saved_sha256}
        return result

    def call(self, op, **args):
        return self.service.handle({'op': op, 'dll': self.dll, **args})


# 包装器保留函数签名供 SDK 生成 schema；写成功后失效缓存，异常统一返回 MCP 工具错误。
def create_server(session=None):
    state = session or WorkbenchSession()
    server = MCPServer('MMTR Workbench', version=__version__, instructions=(
        '独立的 MMTR/MDF 编辑会话，不共享桌面窗口。先 open，再 list/inspect；所有索引从0开始。'
        'Shader 文本分页读取。自动新增参数/Texture2D 必须同时打开关联 MDF 并设置 DXC；'
        '沿用 Workbench 的布局迁移和接口验证。编辑保存在内存，export 必须另存，不能覆盖源文件。'
        '同一 master 的其它使用者也需匹配新布局。MMTR 删除仅支持消费者证明完整的参数/Texture2D；Sampler/UAV 不支持自动迁移。'
        '使用 set_master 显式修改指定材质的MMTR相对路径；不自动改其它材质或安装游戏。工具错误可修正后重试。'))

    def tool(*, write=False, transaction=False, files=False, destructive=False, job=False, job_write=False):
        def decorate(function):
            @wraps(function)
            def invoke(*args, **kwargs):
                with nullcontext() if job else state.lock:
                    backup = deepcopy(state.service) if transaction is True else None
                    mdf_backup = None
                    if transaction == 'mdf':
                        service = state.service
                        mdf_backup = (deepcopy(service.mdf), service.mdf_selected, service.mdf_link_doc,
                                      service.mdf_link_report, service.mdf_migration)
                    saved = state.saved_mmtr
                    try:
                        result = function(*args, **kwargs)
                        if write:
                            state.revision += 1
                            state.invalidate()
                            if isinstance(result, dict):
                                result['revision'] = state.revision
                                if isinstance(result.get('status'), dict):
                                    result['status']['revision'] = state.revision
                        return result
                    except Exception as exc:
                        if backup is not None:
                            state.service = backup
                            state.saved_mmtr = saved
                        if mdf_backup is not None:
                            service = state.service
                            (service.mdf, service.mdf_selected, service.mdf_link_doc,
                             service.mdf_link_report, service.mdf_migration) = mdf_backup
                        if not job:
                            state.invalidate()
                        raise ToolError(str(exc)) from exc
            return server.tool(annotations=ToolAnnotations(
                read_only_hint=not write and not files and not job_write, destructive_hint=destructive,
                open_world_hint=files))(invoke)
        return decorate

    @tool(job=True)
    def workbench_material_preview(actions: MaterialActions, record_index: Index | None = None,
                                    variant: VariantOptions | None = None) -> dict[str, Any]:
        """预览混合参数/贴图批次及关联材质；不编译。提交时使用返回的 digest。"""
        return state.jobs.preview([a.model_dump(exclude_none=True) for a in actions], record_index,
                                  variant=variant.model_dump() if variant is not None else None)

    @tool(job=True, job_write=True)
    def workbench_material_submit(actions: MaterialActions, expected_plan: str,
                                  request_key: Annotated[str, Field(min_length=1, max_length=128)],
                                  record_index: Index | None = None,
                                  variant: VariantOptions | None = None) -> dict[str, Any]:
        """短调用提交后台批次；超时重试必须沿用相同 request_key、动作及计划，防止重复新增。"""
        return state.jobs.submit([a.model_dump(exclude_none=True) for a in actions], record_index,
                                  expected_plan, request_key,
                                  variant=variant.model_dump() if variant is not None else None)

    @tool(job=True)
    def workbench_material_status(job_id: str) -> dict[str, Any]:
        """查询后台阶段/数量/错误；计算期间也可调用，不构建或锁住文档。"""
        return state.jobs.status(job_id)

    @tool(job=True, job_write=True)
    def workbench_material_cancel(job_id: str) -> dict[str, Any]:
        """取消本会话候选并回收其 DXC 进程；已成功提交的任务仍返回 succeeded。"""
        return state.jobs.cancel(job_id)

    @tool(job=True)
    def workbench_material_result(job_id: str, offset: Index = 0, limit: Limit = 40) -> dict[str, Any]:
        """分页读取成功批次的验证结果；Shader 报告分页，文档二进制不会返回。"""
        return state.jobs.result(job_id, offset, limit)

    @tool(job=True, job_write=True, files=True)
    def workbench_variant_export(job_id: str) -> dict[str, Any]:
        """显式导出任务中的独立候选对；目标已绑定计划，源状态保留，不自动安装或打开。"""
        return state.jobs.export_variant(job_id)

    @tool(job=True, job_write=True)
    def workbench_variant_discard(job_id: str) -> dict[str, Any]:
        """丢弃已完成变体任务的内存候选，保留原文档；运行中的任务用 material_cancel。"""
        return state.jobs.discard_variant(job_id)

    @tool(files=True)
    def workbench_project_save(path: str, actions: MaterialActions, record_index: Index | None = None,
                               variant: VariantOptions | None = None, overwrite: bool = False) -> dict[str, Any]:
        """保存不含游戏载荷的 .mmtrproj.json；源必须可从磁盘重开，工程不自动执行。"""
        from .material_project import save_project
        return save_project(state, path, [a.model_dump(exclude_none=True) for a in actions], record_index,
                            variant=variant.model_dump() if variant is not None else None, overwrite=overwrite)

    @tool()
    def workbench_project_read(path: str) -> dict[str, Any]:
        """只读工程意图并与当前源/材质比较；输入变化需重新预览，不返回可执行的旧计划。"""
        from .material_project import read_project
        return read_project(state, path)

    @tool()
    def workbench_status() -> dict[str, Any]:
        """查看版本、DXC、已打开文件、编译缓存和未导出修改；不返回完整大文档。"""
        return state.status()

    @tool(write=True)
    def workbench_configure(dxc_path: str) -> dict[str, Any]:
        """设置本次会话的 dxcompiler.dll 绝对路径；不修改桌面/客户端全局配置。"""
        path = Path(dxc_path).resolve(strict=True)
        if not path.is_file():
            raise EditError('DXC 路径必须是文件。')
        state.dll = str(path)
        return {'dxc_path': state.dll}

    @tool(write=True, transaction=True)
    def workbench_open(mmtr_path: str | None = None, mdf_path: str | None = None,
                       material_index: Index | None = None, discard_unsaved: bool = False) -> dict[str, Any]:
        """原子打开 MMTR/MDF（可一次传两个）；默认拒绝丢弃待替换文档的未导出修改。"""
        if not mmtr_path and not mdf_path:
            raise EditError('请提供 mmtr_path 或 mdf_path。')
        current = state.status()
        for key, path in [('mmtr', mmtr_path), ('mdf', mdf_path)]:
            if path and current[key] and current[key]['unsaved'] and not discard_unsaved:
                raise EditError(f'{key} 有未导出修改；请先导出，或显式指定 discard_unsaved=true。')
        doc = Document.open(mmtr_path) if mmtr_path else None
        mdf = MdfDocument.open(mdf_path) if mdf_path else None
        service = state.service
        if mdf:
            selected = material_index if material_index is not None else (0 if mdf.materials else None)
            if selected is not None:
                mdf.material(selected)
            service.mdf, service.mdf_selected = mdf, selected
        elif material_index is not None:
            service.mdf_selected = state.material_index(material_index)
        if doc:
            service.doc, service.compiled = doc, None
            state.saved_mmtr = hashlib.sha256(doc.original).hexdigest()
        service.mdf_link_doc = service.mdf_link_report = service.mdf_migration = None
        return state.status()

    @tool(write=True)
    def workbench_select_material(material_index: Index) -> dict[str, Any]:
        """选择 MDF 材质，后续未指定 material_index 时使用该材质。"""
        state.service.mdf_selected = state.material_index(material_index)
        return state.status()

    @tool()
    def workbench_list(kind: Literal['shaders', 'passes', 'metadata', 'materials', 'properties', 'textures'],
                       query: str = '', offset: Index = 0, limit: Limit = 40,
                       material_index: Index | None = None, linked_only: bool = True) -> dict[str, Any]:
        """分页搜索，query 为不区分大小写的子串。metadata 包含 UserMaterial 成员和 SRV 名称供 MDF 选择。
        materials 默认仅列与当前 MMTR 路径匹配的材质；仅打开 MDF 时列出全部。
        """
        if kind in ('shaders', 'passes', 'metadata'):
            rows = state.snapshot()[kind]
        elif kind == 'materials':
            if state.service.mdf is None:
                raise EditError('请先打开 MDF。')
            rows = []
            for material in state.service.mdf.materials:
                linked = bool(state.service.doc and master_matches(material['master'], str(state.service.doc.source)))
                if linked_only and state.service.doc and not linked:
                    continue
                rows.append({**{k: material[k] for k in ('index', 'name', 'master', 'property_count', 'texture_count')},
                             'mmtr_linked': linked})
        else:
            index = state.material_index(material_index)
            rows = [{**_public_data(row), 'index': i} for i, row in
                    enumerate(state.service.mdf.material(index)[kind])]
        if query:
            rows = [row for row in rows if query.casefold() in json.dumps(row, ensure_ascii=False).casefold()]
        end = offset + limit
        items = rows[offset:end]
        if kind == 'shaders':
            items = [{**row, 'passes': row['passes'][:8], 'pass_total': len(row['passes'])} for row in items]
        return {'kind': kind, 'total': len(rows), 'offset': offset,
                'next_offset': end if end < len(rows) else None, 'items': items}

    @tool()
    def workbench_inspect(kind: Literal['shader', 'pass', 'material', 'binding', 'changes',
                                      'structure', 'structure_program', 'structure_objects',
                                      'structure_object_users', 'structure_shader_users', 'structure_diagnostics',
                                      'structure_metadata', 'structure_unmodeled_ranges'],
                          index: Index | None = None, offset: Index = 0, limit: Limit = 40) -> dict[str, Any]:
        """查看单项或分页报告。structure 是无需 DXC 的只读结构预检，不能代替寻址/Shader 验证。
        structure_program 的 index 为 Program 索引；structure_object_users 为对象索引；
        structure_shader_users 为 Shader 索引，列出所有引用者。完整 Shader 文本使用 read_shader。
        """
        if kind.startswith('structure'):
            view = {'structure': 'summary', 'structure_program': 'program', 'structure_objects': 'objects',
                    'structure_object_users': 'object_users', 'structure_shader_users': 'shader_users',
                    'structure_diagnostics': 'diagnostics', 'structure_metadata': 'metadata',
                    'structure_unmodeled_ranges': 'unmodeled_ranges'}[kind]
            return state.call('mmtr_inspect_structure', view=view, index=index, offset=offset, limit=limit)
        if kind in ('shader', 'pass'):
            if index is None:
                raise EditError('请指定 index。')
            rows = state.snapshot()['shaders' if kind == 'shader' else 'passes']
            if index >= len(rows):
                raise EditError('索引超出范围。')
            result = dict(rows[index])
            if kind == 'pass':
                return {**page_report(state.require_doc().current_info()['records'][index], offset, limit), **result}
            if kind == 'shader':
                result['sha256'] = hashlib.sha256(state.require_doc().shader_bytes(index)).hexdigest()
                names = result['passes']
                result['pass_total'] = len(names)
                result['passes'] = names[offset:offset+limit]
                result['next_offset'] = offset+limit if offset+limit < len(names) else None
            return result
        if kind == 'material':
            material_index = state.material_index(index)
            material = state.service.mdf.material(material_index)
            return {k: v for k, v in _public_data(material).items() if k not in ('properties', 'textures', 'gpbf')}
        if kind == 'binding':
            material_index = state.material_index(index)
            report = state.service.mdf.binding_report(material_index,
                state.require_doc().current_info() if state.service.doc else None)
        else:
            report = {'mmtr': state.require_doc().build()[1] if state.service.doc else None,
                      'mdf': state.service.mdf.edit_notes if state.service.mdf else []}
        # 所有嵌套列表都限长，列表字段同时附总数；避免一次返回全部变体或资源。
        return page_report(report, offset, limit)

    @tool()
    def workbench_usage(kind: Literal['parameters', 'textures', 'gpbf', 'failures'] = 'parameters',
                        state_filter: Literal['all', 'used', 'descriptor', 'unused', 'unknown'] = 'all',
                        material_index: Index | None = None, offset: Index = 0,
                        limit: Limit = 40) -> dict[str, Any]:
        """统计所有 Shader 对所选 MDF 材质参数、纹理与 GPBF 的实际读取；只读，不修改文档。
        需要同时打开关联 MDF 并设置 DXC。state 为 used/descriptor/unused/unknown：descriptor 表示纹理描述符被读取
        但未构造为资源句柄；complete 为 false 时 unused/descriptor 降为 unknown。
        unused 仅表示当前识别范围内未发现消费者，不证明全部间接访问，不能作为自动删除依据。
        """
        index = state.material_index(material_index, linked=True)
        doc = state.require_doc()
        if not state.dll:
            raise EditError('请先设置DXC路径，再统计材质读取。')
        from .material_plan import dll_identity
        identity = dll_identity(state.dll)
        key = (hashlib.sha256(doc.build()[0]).hexdigest(), state.service.mdf.build()[1]['sha256'], index,
               str(Path(state.dll).resolve()), identity)
        if state.usage_cache is None or state.usage_cache[0] != key:
            candidate = state.call('mmtr_material_usage', material_index=index)
            if dll_identity(state.dll) != identity:
                raise EditError('DXC 在读取统计期间改变，请重试。')
            state.usage_cache = (key, candidate)
        report = state.usage_cache[1]
        rows = report[kind]
        if kind != 'failures' and state_filter != 'all':
            rows = [row for row in rows if row['state'] == state_filter]
        counts = {name: {s: sum(row['state'] == s for row in report[name])
                         for s in ('used', 'descriptor', 'unused', 'unknown')}
                  for name in ('parameters', 'textures', 'gpbf')}
        end = offset + limit
        return {'material_index': index, 'material': report['material'], 'kind': kind,
                'shaders_analyzed': report['shaders_analyzed'], 'complete': report['complete'],
                'coverage': report.get('coverage', {'scope': 'recognized_dxil_reads', 'deletion_supported': False}),
                'shader_count': report.get('shader_count', report['shaders_analyzed']),
                'counts': counts, 'failure_total': len(report['failures']), 'notes': report['notes'],
                'total': len(rows), 'offset': offset, 'next_offset': end if end < len(rows) else None,
                'items': rows[offset:end]}

    @tool(job=True)
    def workbench_removal_preview(actions: RemovalActions,
            request_key: Annotated[str, Field(min_length=1, max_length=128)],
            material_index: Index | None = None, variant: VariantOptions | None = None) -> dict[str, Any]:
        """短调用启动只读删除预检；用 material_status/result 查询、material_cancel 取消。
        验证已覆盖 DXIL 消费者及最终布局；不修改文档、不导出，不能作为运行时删除许可。
        重试必须沿用相同 request_key、动作和材质；eligible 仅对 bounded_dxil_consumers 覆盖成立，提交仍需单独调用 removal_submit。
        """
        return state.jobs.removal_preview([action.model_dump() for action in actions], material_index, request_key,
            variant=None if variant is None else variant.model_dump())

    @tool(job=True, job_write=True, destructive=True)
    def workbench_removal_submit(actions: RemovalActions, expected_plan: str,
            request_key: Annotated[str, Field(min_length=1, max_length=128)], material_index: Index | None = None,
            variant: VariantOptions | None = None) -> dict[str, Any]:
        """执行有界 MMTR/MDF 删除候选；只能提交未使用且完整覆盖的参数/Texture2D，结果重解析验证后更新会话。"""
        return state.jobs.removal_submit([action.model_dump() for action in actions], material_index,
                                         expected_plan, request_key, variant=None if variant is None else variant.model_dump())

    @tool()
    def workbench_effect_template(name: Literal['emission_multiplier']) -> dict[str, Any]:
        """返回已验证范围内的效果模板合同；不修改 MMTR/MDF，不编译或宣称游戏效果通过。"""
        from .effect_templates import effect_template
        return effect_template(name)

    @tool(job=True)
    def workbench_effect_preview(name: Literal['emission_multiplier'], record_index: Index | None = None,
            variant: VariantOptions | None = None) -> dict[str, Any]:
        """将封闭效果模板送入现有只读批量布局计划；不编译 Shader、不修改文档。"""
        from .effect_templates import effect_template
        template = effect_template(name)
        return state.jobs.preview(template['actions'], record_index,
            variant=None if variant is None else variant.model_dump())

    @tool()
    def workbench_read_shader(index: Index, format: Literal['dxil', 'hlsl'] = 'dxil',
                              offset_line: Index = 0,
                              limit: Annotated[int, Field(ge=1, le=200)] = 100) -> dict[str, Any]:
        """分页读取 DXIL 或受限可编译 HLSL；offset_line 从0开始，重建不支持的程序会明确报错。"""
        doc = state.require_doc()
        payload = doc.shader_bytes(index)
        from .material_plan import dll_identity
        identity = dll_identity(state.dll)
        key = (hashlib.sha256(payload).hexdigest(), format, str(Path(state.dll).resolve()), identity)
        cached = state.text_cache.get(key)
        if cached is None:
            result = state.call('disassemble' if format == 'dxil' else 'reconstruct', index=index)
            text = result['ir' if format == 'dxil' else 'source']
            if dll_identity(state.dll) != identity:
                raise EditError('读取期间 DXC 内容已改变，请重新读取。')
            lines = tuple(text.splitlines())
            cached = (lines, {k: result[k] for k in ('entry', 'profile') if k in result})
            state.text_cache.put(key, cached, sys.getsizeof(lines) + sum(map(sys.getsizeof, lines)) + 2048)
        elif dll_identity(state.dll) != identity:
            raise EditError('读取期间 DXC 内容已改变，请重新读取。')
        lines, extra = cached
        end = offset_line + limit
        return {'index': index, 'format': format, 'shader_sha256': key[0], **extra,
                'total_lines': len(lines), 'offset_line': offset_line,
                'next_line': end if end < len(lines) else None, 'text': '\n'.join(lines[offset_line:end])}

    @tool(files=True)
    def workbench_export_shader(path: str, index: Index | None = None,
                                format: Literal['dxbc', 'dxil', 'hlsl', 'compiled'] = 'dxbc') -> dict[str, Any]:
        """另存完整 DXBC/文本或最近编译结果；不在 MCP 响应内传输二进制/完整 IR。"""
        service = state.service
        if format == 'compiled':
            if service.compiled is None:
                raise EditError('请先编译。')
            data = service.compiled
        else:
            if index is None:
                raise EditError('请指定 index。')
            if format == 'dxbc':
                data = state.require_doc().shader_bytes(index)
            else:
                result = state.call('disassemble' if format == 'dxil' else 'reconstruct', index=index)
                data = result['ir' if format == 'dxil' else 'source'].encode('utf-8')
        service.safe_write(path, data)
        return file_result(path, data)

    @tool(write=True)
    def workbench_compile(entry: str, profile: str, source: str | None = None, source_path: str | None = None,
                          apply_index: Index | None = None, expert: bool = False) -> dict[str, Any]:
        """通过隔离 DXC 编译单文件 HLSL；source/source_path 二选一，可严格替换 apply_index。
        失败清除旧编译缓存；默认禁止资源/阶段接口变化，expert 不能代替布局验证。
        """
        if (source is None) == (source_path is None):
            raise EditError('source 与 source_path 必须二选一。')
        text = Path(source_path).read_text(encoding='utf-8-sig') if source_path else source
        result = state.call('compile', source=text, entry=entry, profile=profile)
        if apply_index is not None:
            state.call('apply_compiled', index=apply_index, expert=expert)
        return {'size': result['size'], 'warnings': result['warnings'], 'applied_index': apply_index}

    @tool(write=True, transaction=True)
    def workbench_replace_shader(index: Index, path: str | None = None, use_compiled: bool = False,
                                 expert: bool = False, sync_interface: bool = False) -> dict[str, Any]:
        """导入完整 DXBC 或当前编译结果（二选一）。sync_interface 使用核心事务同步尾部接口。
        接口共享变体不齐时会拒绝；自动材质扩展请使用 add_parameter/add_texture。
        """
        if bool(path) == use_compiled:
            raise EditError('path 与 use_compiled=true 必须二选一。')
        if sync_interface:
            item = {'index': index, **({'path': path} if path else {'use_compiled': True})}
            report = state.call('mmtr_sync_compiled_interfaces', items=[item])['interface_sync']
        else:
            state.call('replace' if path else 'apply_compiled', index=index, path=path, expert=expert)
            report = None
        return {'index': index, 'interface_sync': page_report(report), 'status': state.status()}

    @tool(write=True)
    def workbench_restore_shader(index: Index) -> dict[str, Any]:
        """恢复指定 Shader 原字节；布局迁移后单独恢复可能导致接口不一致，导出仍需通过验证。"""
        state.require_doc().shader_bytes(index)
        state.call('restore_shader', index=index)
        return {'index': index, 'restored': True}

    @tool(write=True, transaction=True)
    def workbench_redirect(record: Index, field: Literal[8, 16, 24, 32, 40, 48, 56],
                            target: Index, expert: bool = False) -> dict[str, Any]:
        """将通道阶段重定向到已有 Shader；field 从 passes.refs 读取，默认严格检查接口和反射。"""
        state.call('redirect', record=record, field=field, target=target, expert=expert)
        return {'record': record, 'field': field, 'target': target}

    @tool(write=True, transaction=True)
    def workbench_rename(pointer: Index, name: Name) -> dict[str, Any]:
        """按 metadata.pointer 在既有字符串容量内改名；不自动改写 Shader 反射或 MDF。"""
        state.call('rename', pointer=pointer, name=name)
        return {'pointer': pointer, 'name': name}

    @tool(write=True, transaction=True)
    def workbench_add_parameter(name: Name, byte_size: Literal[4, 8, 12, 16] = 4,
                                material_index: Index | None = None,
                                values: Values | None = None, sync_mdf: bool = True) -> dict[str, Any]:
        """新增无效果 UserMaterial 尾部参数，自动算偏移并迁移所有受影响 Shader。
        必须打开关联 MDF，默认同步所选材质且零值；任何一步失败都回滚 MMTR 和 MDF。
        """
        index = state.material_index(material_index, linked=True)
        state.service.mdf_selected = index
        values = [0.0] * (byte_size // 4) if values is None else values
        if len(values) * 4 != byte_size:
            raise EditError('values 数量与 byte_size 不一致。')
        buffers = [b for b in state.require_doc().current_info()['metadata']['cbuffers'] if b['name'] == 'UserMaterial']
        if not buffers or len({b['buffer_size'] for b in buffers}) != 1:
            raise EditError('UserMaterial 缺失或存在不同布局。')
        byte_offset = buffers[0]['buffer_size']
        result = state.call('mmtr_add_cbuffer_member', cbuffer='UserMaterial', name=name,
                            byte_size=byte_size, byte_offset=byte_offset, auto_sync=True)
        if sync_mdf:
            state.call('mdf_add_property', material_index=index, name=name, values=values)
        return {'name': name, 'byte_offset': byte_offset, 'byte_size': byte_size, 'material_index': index,
                'mdf_synced': sync_mdf, 'interface_sync': page_report(result['interface_sync'])}

    @tool(write=True, transaction=True)
    def workbench_add_texture(name: Name, record_index: Index, material_index: Index | None = None,
                              path: str = 'systems/rendering/NullBlack.tex', sync_mdf: bool = True) -> dict[str, Any]:
        """给已有 PS 资源表新增无效果 float4 Texture2D，迁移材质尾部并默认同步所选 MDF。
        record_index 从 passes 查询，必须覆盖当前材质纹理集合；未知/不一致布局整体拒绝。
        """
        index = state.material_index(material_index, linked=True)
        state.service.mdf_selected = index
        result = state.call('mmtr_add_resource', kind='srvs', record_index=record_index, name=name, auto_sync=True)
        if sync_mdf:
            state.call('mdf_add_texture', material_index=index, name=name, path=path)
        return {'name': name, 'material_index': index, 'mdf_synced': sync_mdf,
                'interface_sync': page_report(result['interface_sync'])}

    @tool(write=True, transaction='mdf')
    def workbench_set_master(path: str, material_index: Index | None = None) -> dict[str, Any]:
        """将指定MDF材质改用另一个MMTR，例如MaterialShader/Variation/test.mmtr（不带版本后缀）。
        只改所选材质，不改其它共享master的材质；不复制MMTR或证明新Shader兼容。
        导出MMTR/MDF后重新打开匹配路径可检查关联；路径错误或重解析失败整体回滚。
        """
        index = state.material_index(material_index)
        old = state.service.mdf.material(index)['master']
        state.call('mdf_set_master', material_index=index, path=path)
        state.service.mdf.build()
        master = state.service.mdf.material(index)['master']
        return {'material_index': index, 'old_master': old, 'master': master,
                'mmtr_linked': bool(state.service.doc and master_matches(master, str(state.service.doc.source)))}

    @tool(write=True, transaction='mdf')
    def workbench_edit_mdf(actions: Annotated[list[Action], Field(min_length=1, max_length=100)]) -> dict[str, Any]:
        """批量编辑 MDF 参数、分量、贴图、设置或删除槽位；整批失败全部回滚。
        add_* 只使用当前 MMTR 现有接口；新增真正接口应先调用 add_parameter/add_texture。
        删除 MDF 项不等于删除 MMTR Shader 接口；请用 binding 检查剩余绑定。
        """
        for action in actions:
            if action.action in ('add_property', 'add_texture') and state.service.doc:
                state.material_index(action.material_index, linked=True)
            args = action.model_dump(exclude={'action'}, exclude_none=True)
            state.call('mdf_' + action.action, **args)
        return {'applied': len(actions), 'status': state.status()}

    @tool()
    def workbench_validate(offset: Index = 0, limit: Limit = 40,
                           scope: Literal['metadata_delta', 'associated_layout'] = 'metadata_delta') -> dict[str, Any]:
        """只读验证，问题列表分页。默认只检查 metadata 修改；无修改返回 skipped/valid=null。
        associated_layout 需关联 MDF 和 DXC，逐 Shader 检查有界读取、反射及材质标记；未知访问失败。
        报告列出实际覆盖数量，不验证外部材质或游戏效果。
        """
        report = state.call('mmtr_validate_interfaces', scope=scope)
        return page_report(report, offset, limit)

    @tool(write=True, transaction=True, files=True)
    def workbench_export(mmtr_path: str | None = None, mdf_path: str | None = None,
                         report_path: str | None = None) -> dict[str, Any]:
        """验证并另存 MMTR/MDF/JSON 报告。先预检所有目标；多文件写失败恢复原目标。
        保留显式 set_master 的编辑，不自动改引用或安装游戏；源文件仍必须另存。
        """
        if not any((mmtr_path, mdf_path, report_path)):
            raise EditError('请指定至少一个导出路径。')
        service = state.service
        payloads, report = [], {}
        if mmtr_path or (report_path and service.doc):
            doc = state.require_doc()
            if doc.metadata_edits:
                validation = state.call('mmtr_validate_interfaces')
                if not validation['valid']:
                    raise EditError('MMTR 接口验证失败，禁止导出：' + json.dumps(page_report(validation), ensure_ascii=False))
            data, report['mmtr'] = doc.build()
            if mmtr_path:
                payloads.append((mmtr_path, data))
        if mdf_path or (report_path and service.mdf):
            if mdf_path and not mdf_path.lower().endswith('.mdf2.45'):
                raise EditError('MDF 路径必须以 .mdf2.45 结尾。')
            data, report['mdf'] = service.mdf_write_snapshot()
            if mdf_path:
                payloads.append((mdf_path, data))
        if report_path:
            payloads.append((report_path, json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')))
        paths = []
        for path, _ in payloads:
            path = service.check_write_target(path)
            if not path.parent.is_dir():
                raise EditError(f'导出目录不存在：{path.parent}')
            if path.exists() and not path.is_file():
                raise EditError(f'目标不是文件：{path}')
            if any(path == p or (path.exists() and p.exists() and path.samefile(p)) for p in paths):
                raise EditError('多个导出目标指向同一文件。')
            paths.append(path)
        backups = [path.read_bytes() if path.exists() else None for path in paths]
        written = []
        try:
            for i, ((_, data), path) in enumerate(zip(payloads, paths)):
                atomic_write(path, data)
                written.append(i)
        except Exception as exc:
            failures = []
            for i in reversed(written):
                try:
                    if backups[i] is None:
                        paths[i].unlink()
                    else:
                        atomic_write(paths[i], backups[i])
                except OSError as rollback:
                    failures.append(f'{paths[i]}: {rollback}')
            if failures:
                raise EditError(f'导出失败：{exc}；恢复失败：' + '; '.join(failures)) from exc
            raise
        if mmtr_path:
            state.saved_mmtr = report['mmtr']['sha256']
        if mdf_path:
            service.mdf.saved_sha256 = report['mdf']['sha256']
        return {'files': [file_result(path, data) for (path, data) in payloads]}

    @tool(write=True, transaction=True, destructive=True)
    def workbench_reset(target: Literal['mmtr', 'mdf', 'all']) -> dict[str, Any]:
        """丢弃指定文档的全部内存编辑，恢复打开时的源字节；不会删除磁盘导出文件。"""
        if target in ('mmtr', 'all') and state.service.doc:
            state.call('reset')
        if target in ('mdf', 'all') and state.service.mdf:
            state.call('mdf_reset')
        return state.status()

    return server


# 报告递归分页，不截断标量；工具调用方可用 offset 继续读取各列表字段。
def page_report(value, offset=0, limit=40):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[key] = page_report(item, offset, limit)
            if isinstance(item, list):
                result[key + '_total'] = len(item)
                result[key + '_next_offset'] = offset+limit if offset+limit < len(item) else None
        return result
    if isinstance(value, list):
        return [page_report(item, 0, limit) for item in value[offset:offset+limit]]
    return value


# 响应仅含文件身份，不包含大块二进制；SHA 用于客户端核对另存产物。
def file_result(path, data):
    return {'path': str(Path(path).resolve()), 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def main():
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
    state = WorkbenchSession()
    try:
        create_server(state).run(transport='stdio')
    finally:
        state.close()


if __name__ == '__main__':
    main()
