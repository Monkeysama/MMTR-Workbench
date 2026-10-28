"""带 schema 的轻量材质工程：保存修改意图与输入身份，不保存载荷或可执行旧计划。

读取只比较已打开的会话，不自动打开工程引用、不运行 Shader、不写输出文件。
源改变时返回需要重新规划；路径或材质身份错配明确拒绝。
"""
import hashlib
import json
import math
from pathlib import Path
import re
import struct

from . import __version__
from .material_plan import dll_identity
from .material_variant import validate_variant_master
from .mdf import mdf_suffix_ok
from .model import EditError, atomic_write

MAX_PROJECT_BYTES = 2 * 1024 * 1024


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(value)-set(required)-set(optional) or not set(required) <= set(value):
        raise EditError('工程字段缺失、类型错误或包含未知字段。')


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise EditError('工程 SHA256 字段无效。')


def _path(value, suffix=None):
    if (not isinstance(value, str) or not value or len(value) > 32767 or '\x00' in value
            or not Path(value).is_absolute() or (suffix and not value.casefold().endswith(suffix))):
        raise EditError('工程路径必须是有效的绝对文件路径。')


def _request(value):
    _keys(value, ('actions', 'record_index', 'variant'))
    actions, record, variant = value['actions'], value['record_index'], value['variant']
    if type(record) is not int and record is not None or (type(record) is int and record < 0):
        raise EditError('工程 record_index 必须是非负整数或 null。')
    if not isinstance(actions, list) or not 1 <= len(actions) <= 100:
        raise EditError('工程需要 1..100 个材质追加动作。')
    names = set()
    for item in actions:
        if not isinstance(item, dict):
            raise EditError('工程动作类型无效。')
        kind = item.get('action')
        _keys(item, ('action', 'name', 'byte_size', 'values') if kind == 'parameter' else ('action', 'name', 'path'))
        name = item['name']
        if (not isinstance(name, str) or len(name) > 256 or not re.fullmatch('[A-Za-z_]\\w*', name, re.ASCII)
                or name in names):
            raise EditError('工程接口名称无效或重复。')
        names.add(name)
        if kind == 'parameter':
            values, width = item['values'], item['byte_size']
            if (type(width) is not int or width not in (4, 8, 12, 16) or not isinstance(values, list)
                    or len(values)*4 != width or any(type(x) not in (int, float) or not math.isfinite(x) for x in values)):
                raise EditError('工程参数宽度或默认值无效。')
            try:
                struct.pack('<'+'f'*len(values), *values)
            except (OverflowError, struct.error) as exc:
                raise EditError('工程参数超出 float32 范围。') from exc
        elif kind != 'texture' or not isinstance(item['path'], str) or not item['path'].strip() or '\x00' in item['path']:
            raise EditError('工程纹理动作无效。')
    if variant is not None:
        _keys(variant, ('master', 'mmtr_path', 'mdf_path', 'overwrite'))
        validate_variant_master(variant['master'])
        _path(variant['mmtr_path'])
        _path(variant['mdf_path'])
        if not mdf_suffix_ok(variant['mdf_path']):
            raise EditError('工程 MDF 输出路径必须以已支持的 .mdf2.N 结尾。')
        if type(variant['overwrite']) is not bool:
            raise EditError('工程 overwrite 必须是布尔值。')
    return value


def validate_project(value):
    """封闭 schema，只返回 JSON 意图；偏移、载荷、可执行计划和任意命令均不在字段中。"""
    _keys(value, ('schema', 'kind', 'tool_version', 'sources', 'dxc', 'material', 'request', 'validation'))
    if type(value['schema']) is not int or value['schema'] != 1 or value['kind'] != 'mmtr-material-project':
        raise EditError('不支持的材质工程 schema/kind。')
    if not isinstance(value['tool_version'], str) or len(value['tool_version']) > 64:
        raise EditError('工程工具版本无效。')
    _keys(value['sources'], ('mmtr', 'mdf'))
    for kind in ('mmtr', 'mdf'):
        source = value['sources'][kind]
        _keys(source, ('path', 'sha256'))
        _path(source['path'])
        _sha(source['sha256'])
    _keys(value['dxc'], ('path', 'sha256'))
    _path(value['dxc']['path'])
    _sha(value['dxc']['sha256'])
    material = value['material']
    _keys(material, ('index', 'name', 'master'))
    if type(material['index']) is not int or material['index'] < 0 or not isinstance(material['name'], str):
        raise EditError('工程材质身份无效。')
    if not isinstance(material['master'], str):
        raise EditError('工程 master 身份无效。')
    validate_variant_master(material['master'].replace('\\', '/'))
    _request(value['request'])
    _keys(value['validation'], ('state', 'game_tested'))
    if value['validation'] != {'state': 'requires_fresh_validation', 'game_tested': False}:
        raise EditError('工程验证摘要不是可复用的 Shader 验证证明。')
    return value


def _identity(service):
    if service.doc is None or service.mdf is None or service.mdf_selected is None:
        raise EditError('工程需要已打开的 MMTR、MDF 和所选关联材质。')
    material = service.mdf.material(service.mdf_selected)
    return {'index': material['index'], 'name': material['name'], 'master': material['master']}


def create_project(owner, actions, record_index=None, *, variant=None):
    """仅保存可从磁盘重开的源；未另存的编辑不能靠缺少载荷的工程假装可恢复。"""
    with owner.lock:
        service = owner.service
        identity = _identity(service)
        plan = owner.jobs.preview(actions, record_index, variant=variant)
        parameters, textures = iter(plan['parameters']), iter(plan['textures'])
        normalized = []
        for kind in plan['action_order']:
            item = next(parameters if kind == 'parameter' else textures)
            normalized.append(dict(action=kind, **{key: item[key] for key in
                (('name', 'byte_size', 'values') if kind == 'parameter' else ('name', 'path'))}))
        sources = {}
        for kind, document in (('mmtr', service.doc), ('mdf', service.mdf)):
            if not document.source or not Path(document.source).is_file():
                raise EditError('工程源必须是可重开的本地文件。')
            path = Path(document.source).resolve()
            digest = hashlib.sha256(document.build()[0]).hexdigest()
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise EditError('工程源存在未另存编辑或磁盘变化；请先另存并重新打开后创建工程。')
            sources[kind] = {'path': str(path), 'sha256': digest}
        variant = None if variant is None else {
            'master': plan['variant']['edit']['variant_master'], 'mmtr_path': plan['variant']['mmtr']['path'],
            'mdf_path': plan['variant']['mdf']['path'], 'overwrite': variant.get('overwrite', False)}
        result = {'schema': 1, 'kind': 'mmtr-material-project', 'tool_version': __version__,
                  'sources': sources, 'dxc': {'path': str(Path(owner.dll).resolve()), 'sha256': dll_identity(owner.dll)},
                  'material': identity, 'request': {'actions': normalized, 'record_index': record_index, 'variant': variant},
                  'validation': {'state': 'requires_fresh_validation', 'game_tested': False}}
        # tuple 默认值在最终 JSON 中统一变成列表。
        return validate_project(json.loads(json.dumps(result, allow_nan=False)))


def save_project(owner, path, actions, record_index=None, *, variant=None, overwrite=False):
    """显式另存工程，保护输入及变体目标；普通失败沿用 atomic_write 保留旧文件。"""
    if type(overwrite) is not bool:
        raise EditError('工程 overwrite 必须是布尔值。')
    with owner.lock:
        value = create_project(owner, actions, record_index, variant=variant)
        path = owner.service.check_write_target(path)
        if not str(path).casefold().endswith('.mmtrproj.json'):
            raise EditError('工程文件必须以 .mmtrproj.json 结尾。')
        protected = [Path(owner.dll).resolve()]
        if value['request']['variant']:
            protected += [Path(value['request']['variant'][k]) for k in ('mmtr_path', 'mdf_path')]
        if any(path == p or (path.exists() and p.exists() and path.samefile(p)) for p in protected):
            raise EditError('工程不能覆盖 DXC 或变体输出文件。')
        if path.exists() and (not path.is_file() or not overwrite):
            raise EditError('工程目标已存在；覆盖须显式指定 overwrite。')
        data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
        if len(data) > MAX_PROJECT_BYTES:
            raise EditError('工程超过2MiB上限。')
        atomic_write(path, data)
        return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}


def read_project(owner, path):
    """只读取工程并与当前会话比较；不自动打开路径，不编译，不导出或复用旧偏移。"""
    path = Path(path)
    if path.stat().st_size > MAX_PROJECT_BYTES:
        raise EditError('工程超过2MiB上限。')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise EditError('工程包含重复 JSON 字段。')
            result[key] = value
        return result
    try:
        value = validate_project(json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EditError('工程不是有效的 UTF-8 JSON。') from exc
    with owner.lock:
        service = owner.service
        if _identity(service) != value['material']:
            raise EditError('工程所选材质身份与当前会话不匹配。')
        changed = []
        for kind, document in (('mmtr', service.doc), ('mdf', service.mdf)):
            source = value['sources'][kind]
            if not document.source or str(Path(document.source).resolve()) != source['path']:
                raise EditError('工程源路径与当前打开文件不匹配。')
            if hashlib.sha256(document.build()[0]).hexdigest() != source['sha256']:
                changed.append(kind)
        if dll_identity(owner.dll) != value['dxc']['sha256']:
            changed.append('dxc')
        if value['tool_version'] != __version__:
            changed.append('tool_version')
        return {'project': value, 'changed_inputs': changed, 'requires_replan': True,
                'can_execute_saved_plan': False, 'source_state': 'preserved'}
