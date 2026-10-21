"""不可变的材质追加计划；只读文档，明确源身份、动作顺序和最终布局。

每个参数沿用单项追加后按 16B 对齐的规则。这里不编译、不写文件或提交文档。
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import struct

from .material_addresses import MaterialTailLayout
from .model import EditError


@dataclass(frozen=True)
class ParameterAddition:
    """一个尾部 float1..4 的确定位置及 MDF 默认值。"""
    name: str
    byte_size: int
    byte_offset: int
    values: tuple[float, ...]

    def declaration(self):
        return {k: v for k, v in asdict(self).items() if k != 'values'}


@dataclass(frozen=True)
class TextureAddition:
    """单个无消费者 Texture2D 的材质槽身份和 MDF 路径。"""
    name: str
    path: str


@dataclass(frozen=True)
class MaterialEditPlan:
    """会话快照上的有界计划；源 revision/内容/DXC 改变时须重建。"""
    source_sha256: str
    source_revision: int
    mdf_sha256: str | None
    dxc_sha256: str
    old: MaterialTailLayout
    new: MaterialTailLayout
    parameters: tuple[ParameterAddition, ...]
    textures: tuple[TextureAddition, ...]
    action_order: tuple[str, ...]
    target_materials: tuple[int, ...]
    tables: tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]
    texture_shaders: tuple[int, ...]
    shader_candidates: tuple[int, ...]
    variant_master: str | None = None

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, ensure_ascii=True,
                                         allow_nan=False).encode()).hexdigest()

    def report(self):
        return {**asdict(self), 'digest': self.digest,
                'shader_analysis': 'pending', 'game_tested': False}


def dll_identity(dll):
    return hashlib.sha256(Path(dll).read_bytes()).hexdigest()


# 完整读取已支持的材质布局；纹理批次共享同一个绘制记录范围，不创造新阶段/资源类型。
def plan_material_edit(document, material, dll, actions, record_index=None, *, source_revision=0,
                       mdf_sha256=None, target_materials=(), variant_master=None):
    from .material_extension import current_layout, texture_tables
    if not isinstance(actions, list) or not 1 <= len(actions) <= 100:
        raise EditError('批次需要 1..100 个参数/纹理动作。')
    if any(op['kind'] != 'extend_material_interface' for op in document.metadata_edits):
        raise EditError('请先完成或重置已有手动metadata修改，再自动扩展材质接口。')
    old = current_layout(document, material)
    info = document.current_info()
    existing = {m['name'] for cb in info['metadata']['cbuffers'] if cb['name'] == 'UserMaterial' for m in cb['members']}
    existing.update(e['name'] for e in info['metadata']['srvs'])
    existing.update(old.texture_names+old.gpbf_names)
    names, parameters, textures, order = set(), [], [], []
    size = old.parameter_bytes
    for action in actions:
        if not isinstance(action, dict) or action.get('action') not in ('parameter', 'texture'):
            raise EditError('批次仅支持 parameter/texture 追加动作。')
        kind, name = action['action'], action.get('name')
        allowed = {'action', 'name', 'byte_size', 'byte_offset', 'values'} if kind == 'parameter' else {'action', 'name', 'path'}
        if action.keys()-allowed:
            raise EditError('批次动作含未知字段。')
        if not isinstance(name, str) or len(name) > 256 or not re.fullmatch(r'[A-Za-z_]\w*', name, re.ASCII):
            raise EditError('新增接口名称必须是ASCII标识符。')
        if name in names or name in existing:
            raise EditError(f'接口名称重复或已存在：{name}')
        names.add(name)
        order.append(kind)
        if kind == 'parameter':
            width = action.get('byte_size')
            if type(width) is not int or width not in (4, 8, 12, 16) or action.get('byte_offset', size) != size:
                raise EditError('自动扩展支持4/8/12/16字节参数，偏移必须等于当前UserMaterial大小。')
            values = action.get('values', [0.0]*(width//4))
            if (not isinstance(values, (list, tuple)) or len(values)*4 != width or
                    any(type(x) not in (int, float) or not math.isfinite(x) for x in values)):
                raise EditError('参数默认值数量/类型与 byte_size 不一致或包含非有限值。')
            try:
                struct.pack('<'+'f'*len(values), *values)
            except (OverflowError, struct.error) as exc:
                raise EditError('参数默认值超出 float32 范围。') from exc
            parameters.append(ParameterAddition(name, width, size, tuple(float(x) for x in values)))
            size = (size+width+15)&~15
            if size > 0xffff:
                raise EditError('UserMaterial超过16位布局范围。')
        else:
            path = action.get('path', 'systems/rendering/NullBlack.tex')
            if not isinstance(path, str) or not path.strip() or '\0' in path:
                raise EditError('纹理路径不能为空或含 NUL。')
            try:
                if len(path.encode('utf-16-le')) > 65534:
                    raise EditError('纹理路径过长。')
            except UnicodeError as exc:
                raise EditError('纹理路径不是有效的 UTF-16 文本。') from exc
            textures.append(TextureAddition(name, path))
    tables, texture_shaders = {}, set()
    if textures:
        if type(record_index) is not int:
            raise EditError('新增纹理必须指定绘制 record_index。')
        tables, texture_shaders = texture_tables(document, old, record_index)
        for records in tables.values():
            for i in records:
                record = info['records'][i]
                if len(record['metadata_tables']['srvs'])+len(textures) > 255 or record['per_stage_counts_raw']['srvs'][4]+len(textures) > 255:
                    raise EditError('SRV数量超出单字节范围。')
    new = MaterialTailLayout(size, old.texture_names+tuple(t.name for t in textures), old.gpbf_names)
    if any(cb['member_count']+len(parameters) > 4096 for cb in info['metadata']['cbuffers'] if cb['name'] == 'UserMaterial'):
        raise EditError('UserMaterial成员数量超出已验证范围。')
    return MaterialEditPlan(hashlib.sha256(document.build()[0]).hexdigest(), source_revision, mdf_sha256,
        dll_identity(dll), old, new, tuple(parameters), tuple(textures), tuple(order), tuple(target_materials),
        tuple((offsets, tuple(records)) for offsets, records in tables.items()), tuple(sorted(texture_shaders)),
        tuple(s['index'] for s in info['shaders']), variant_master)
