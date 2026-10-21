"""材质输入读取统计：只读分析各 Shader 对 MDF 参数、纹理与 GPBF 的实际读取，不修改文档。

读取来源与 material_addresses 的迁移规则一致：heap 材质 CB、BindlessBuffer、UserMaterialInstances，
另加静态绑定的 UserMaterial 常量缓冲与按名称绑定的纹理。无法证明身份的读取使对应类别标为不完整，
不把未知读取当作“未读取”。
"""
import hashlib
import re

from .dxil_sources import parse, handle_sources
from .material_addresses import material_reads

_STATIC_CB = re.compile(r'call %dx\.types\.CBufRet\.(i32|f32|f16|i16) @dx\.op\.cbufferLoadLegacy\.\1\('
                        r'i32 59, %dx\.types\.Handle (%[\w.]+), i32 (\d+|%[\w.]+)\)')
_EXTRACT = r'extractvalue %dx\.types\.CBufRet\.{kind} {value}, ([0-7])'
_LANE_BYTES = {'i32': 4, 'f32': 4, 'f16': 2, 'i16': 2}


# 沿 SSA 使用链向前查找 heap 句柄构造；只用于区分“读取了描述符”与“用作资源句柄”。
def _reaches_heap_handle(uses, ssa, limit=64):
    pending, visited = [ssa], set()
    while pending and len(visited) < limit:
        value = pending.pop()
        if value in visited:
            continue
        visited.add(value)
        for row in uses.get(value, ()):
            if '@dx.op.createHandleFromHeap' in row['text']:
                return True
            if row['value']:
                pending.append(row['value'])
    return False


# 单个 Shader 的读取：record 为材质记录字节偏移（参数区与尾部字段），cbuffer 为静态 UserMaterial 偏移；
# record_handles 为其值流入 createHandleFromHeap 的记录偏移。
def shader_input_reads(ir, cbuffer_name='UserMaterial'):
    definitions, uses, _ = parse(ir)
    result = {'record': set(), 'record_handles': set(), 'cbuffer': set(), 'textures': set(), 'errors': []}
    try:
        for read in material_reads(ir):
            for extract in read['extracts']:
                # 无消费者的 extractvalue 不代表实际取值。
                if uses.get(extract['ssa']):
                    result['record'].add(extract['offset'])
                    if _reaches_heap_handle(uses, extract['ssa']):
                        result['record_handles'].add(extract['offset'])
    except ValueError as exc:
        result['errors'].append(('record', str(exc)))
    origins = handle_sources(ir, definitions)
    static_cb = set()
    for value, origin in origins.items():
        binding = origin.get('binding')
        if origin.get('kind') != 'named-resource' or not binding:
            continue
        if binding['resource_class'] == 2 and binding['name'] == cbuffer_name:
            static_cb.add(value)
        elif binding['resource_class'] == 0 and uses.get(value):
            result['textures'].add(binding['name'])
    for value, row in definitions.items():
        load = _STATIC_CB.fullmatch(row['text'])
        if not load or load[2] not in static_cb:
            continue
        if not load[3].isdigit():
            result['errors'].append(('cbuffer', f'{cbuffer_name} 动态行索引：{row["text"]}'))
            continue
        lane_bytes = _LANE_BYTES[load[1]]
        pattern = re.compile(_EXTRACT.format(kind=load[1], value=re.escape(value)))
        for user in uses[value]:
            extract = pattern.fullmatch(user['text'])
            if not extract:
                result['errors'].append(('cbuffer', f'{cbuffer_name} aggregate有未支持的消费者：{user["text"]}'))
                continue
            if uses.get(user['value']):
                result['cbuffer'].add(int(load[3]) * 16 + int(extract[1]) * lane_bytes)
    return result


def _state(read, complete):
    return 'used' if read else ('unused' if complete else 'unknown')


# 汇总全部不同载荷的 Shader；layout 为当前尾部布局，material 为 MDF 公开数据。
def material_input_usage(document, material, layout, disassemble):
    info = document.current_info()
    record_reads, record_handles, cbuffer_reads, named_textures = {}, {}, {}, {}
    failures, seen = [], {}
    for shader in info['shaders']:
        payload = document.shader_bytes(shader['index'])
        key = hashlib.sha256(payload).digest()
        if key in seen:
            continue
        seen[key] = shader['index']
        try:
            reads = shader_input_reads(disassemble(payload))
        except Exception as exc:  # 反汇编失败的 Shader 使两类结果都不完整。
            failures.append({'shader': shader['index'], 'scope': 'all', 'error': str(exc)})
            continue
        for scope, error in reads['errors']:
            failures.append({'shader': shader['index'], 'scope': scope, 'error': error})
        for offset in reads['record']:
            record_reads.setdefault(offset, set()).add(shader['index'])
        for offset in reads['record_handles']:
            record_handles.setdefault(offset, set()).add(shader['index'])
        for offset in reads['cbuffer']:
            cbuffer_reads.setdefault(offset, set()).add(shader['index'])
        for name in reads['textures']:
            named_textures.setdefault(name, set()).add(shader['index'])

    # 静态 CB 的失败只影响参数；材质记录或反汇编失败同时影响参数与尾部字段。
    parameters_complete = not failures
    tail_complete = not any(f['scope'] in ('record', 'all') for f in failures)
    notes = []
    if any(material.get('mmtrs') or ()):
        # MMTRS（shader LOD 重定向）改变尾部字段顺序与起点，当前布局模型不覆盖。
        tail_complete = False
        notes.append('材质含 MMTRS（shader LOD 重定向），尾部字段位置未按重定向解析，纹理/GPBF 结果不完整。')
    fields = layout.fields()
    by_offset = {offset: name for name, offset in fields.items()}
    unmapped = sorted(o for o in record_reads if o >= layout.parameter_bytes and o not in by_offset)
    if unmapped:
        tail_complete = False
        notes.append(f'尾部读取偏移无法对应 MDF 字段：{unmapped[:16]}')

    parameter_reads = {}
    for source in (record_reads, cbuffer_reads):
        for offset, shaders in source.items():
            if offset < layout.parameter_bytes:
                parameter_reads.setdefault(offset, set()).update(shaders)
    parameters = []
    for prop in material['properties']:
        start, end = prop['offset'], prop['offset'] + prop['byte_size']
        shaders = set().union(*(s for o, s in parameter_reads.items() if start <= o < end))
        parameters.append({'name': prop['name'], 'offset': start, 'byte_size': prop['byte_size'],
                           'state': _state(shaders, parameters_complete), 'shader_count': len(shaders)})

    # 纹理：值流入 heap 句柄或按名称绑定为 used；只读取描述符值为 descriptor（含反馈/上报用途）。
    def tail_rows(names, textures):
        rows = []
        for name in names:
            readers = set(record_reads.get(fields[name], ()))
            if textures:
                handles = set(record_handles.get(fields[name], ())) | named_textures.get(name, set())
                state = 'used' if handles else ('descriptor' if readers else _state((), tail_complete))
                if state == 'descriptor' and not tail_complete:
                    state = 'unknown'
                readers |= handles
            else:
                state = _state(readers, tail_complete)
            rows.append({'name': name, 'record_offset': fields[name], 'state': state,
                         'shader_count': len(readers)})
        return rows

    return {
        'material': material.get('name'),
        'parameter_bytes': layout.parameter_bytes,
        'shaders_analyzed': len(seen),
        'complete': {'parameters': parameters_complete, 'textures': tail_complete},
        'parameters': parameters,
        'textures': tail_rows(layout.texture_names, True),
        'gpbf': tail_rows(layout.gpbf_names, False),
        'failures': failures,
        'notes': notes,
    }
