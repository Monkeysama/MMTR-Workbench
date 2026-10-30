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
            if re.search(r'\bcall\b', row['text']) and '@dx.op.' not in row['text']:
                return None
            if row['value']:
                pending.append(row['value'])
    return None if pending else False


def _material_handle_gaps(definitions, uses, origins, recognized_loads, cbuffer_name):
    """检查材质句柄的全部消费者，包括没有赋值结果的调用；逃逸不能成为无读取证明。"""
    handles = {}
    for value, origin in origins.items():
        name = origin.get('binding', {}).get('name')
        if origin.get('kind') == 'named-resource':
            if name == cbuffer_name:
                handles[value] = 'cbuffer'
            elif name in ('BindlessBuffer', 'UserMaterialInstances'):
                handles[value] = 'record'
        annotation = origin.get('annotation', {}).get('text', '')
        if origin.get('kind') == 'heap-descriptor' and re.search(
                r'%dx.types.ResourceProperties \{ i32 13, i32 \d+ \}', annotation):
            handles[value] = 'record'
            parent = re.search(r'%dx.types.Handle (%[\w.]+), %dx.types.ResourceProperties', annotation)
            if parent:
                handles[parent[1]] = 'record'
    # 注解可以形成多层别名；即使后续注解声称另一种资源类型，仍跟踪原材质句柄。
    changed = True
    while changed:
        changed = False
        for value, row in definitions.items():
            annotation = re.fullmatch(r'call %dx.types.Handle @dx.op.annotateHandle\('
                r'i32 216, %dx.types.Handle (%[\w.]+), .*', row['text'])
            if annotation and annotation[1] in handles and value not in handles:
                handles[value] = handles[annotation[1]]
                changed = True
    errors = []
    for handle, scope in handles.items():
        for user in uses.get(handle, ()):
            text = user['text']
            if text.startswith('call %dx.types.Handle @dx.op.annotateHandle(') and user['value'] in handles:
                continue
            if scope == 'cbuffer' and _STATIC_CB.fullmatch(text):
                continue
            if user['value'] in recognized_loads:
                continue
            errors.append((scope, f'材质句柄有未覆盖的消费者：{text}'))
    return errors


# 单个 Shader 的读取：record 为材质记录字节偏移（参数区与尾部字段），cbuffer 为静态 UserMaterial 偏移；
# record_handles 为其值流入 createHandleFromHeap 的记录偏移。
def _function_input_reads(ir, cbuffer_name, header_bytes=0):
    definitions, uses, _ = parse(ir)
    result = {'record': set(), 'record_handles': set(), 'instance': set(), 'cbuffer': set(), 'textures': set(),
              'errors': []}
    recognized_loads = set()
    try:
        for read in material_reads(ir, header_bytes=header_bytes):
            recognized_loads.add(read['ssa'])
            for extract in read['extracts']:
                # 无消费者的 extractvalue 不代表实际取值。
                if uses.get(extract['ssa']):
                    # 参数实例流只含参数区；其偏移不能解释为尾部纹理/GPBF字段。
                    result['instance' if read['kind'] == 'instance' else 'record'].add(extract['offset'])
                    reaches = _reaches_heap_handle(uses, extract['ssa'])
                    if reaches:
                        result['record_handles'].add(extract['offset'])
                    elif reaches is None:
                        result['errors'].append(('record', '描述符使用链超出分析范围或进入未知函数。'))
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
    # 未识别的读取形式或句柄逃逸必须影响覆盖度，不能因正则未匹配就解释为没有读取。
    result['errors'].extend(_material_handle_gaps(definitions, uses, origins, recognized_loads, cbuffer_name))
    for value, row in definitions.items():
        if re.search(r'@dx.op\.(?:cbufferLoad|rawBufferLoad|bufferLoad)', row['text']):
            match = re.search(r'%dx.types.Handle (%[\w.]+)', row['text'])
            origin = origins.get(match[1], {}) if match else {}
            if not origin or (origin.get('kind') == 'heap-descriptor' and value not in recognized_loads):
                result['errors'].append(('all', f'缓冲读取来源或寻址未被覆盖：{row["text"]}'))
        if ('%dx.types.Handle' in row['text'] and row['text'].startswith(('phi ', 'select '))):
            result['errors'].append(('all', '存在未分析的资源句柄合流。'))
    return result


# SSA 名称只在函数内有意义；反射绑定为模块共享，函数正文逐个独立分析后合并。
def shader_input_reads(ir, cbuffer_name='UserMaterial', header_bytes=0):
    bindings = '\n'.join(line for line in ir.splitlines() if line.startswith('; '))
    functions = re.findall(r'^define .*?^}', ir, flags=re.M | re.S)
    result = {'record': set(), 'record_handles': set(), 'instance': set(), 'cbuffer': set(), 'textures': set(),
              'errors': []}
    if not functions:
        result['errors'].append(('all', '没有可独立分析的 DXIL 函数。'))
    for body in functions:
        reads = _function_input_reads(bindings+'\n'+body, cbuffer_name, header_bytes)
        for key, values in reads.items():
            if key == 'errors':
                result[key].extend(values)
            else:
                result[key].update(values)
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
        if key not in seen:
            try:
                seen[key] = shader_input_reads(disassemble(payload), header_bytes=layout.header_bytes)
            except Exception as exc:  # 反汇编失败的 Shader 使两类结果都不完整。
                seen[key] = {'errors': [('all', str(exc))]}
        reads = seen[key]
        for scope, error in reads['errors']:
            failures.append({'shader': shader['index'], 'scope': scope, 'error': error})
        if 'record' not in reads:
            continue
        for offset in reads['instance']:
            if offset >= layout.parameter_bytes:
                failures.append({'shader': shader['index'], 'scope': 'record',
                                 'error': f'参数实例读取偏移 {offset} 超出参数区，无法证明实例编号。'})
            else:
                cbuffer_reads.setdefault(offset, set()).add(shader['index'])
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
    redirected = any(material.get('mmtrs') or ())
    if redirected:
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
            readers = set(record_reads.get(fields[name], ())) if not redirected else set()
            if textures:
                handles = (set(record_handles.get(fields[name], ())) if not redirected else set()) | named_textures.get(name, set())
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
        'shader_count': len(info['shaders']),
        'coverage': {'scope': 'recognized_dxil_reads', 'deletion_supported': False},
        'complete': {'parameters': parameters_complete, 'textures': tail_complete},
        'parameters': parameters,
        'textures': tail_rows(layout.texture_names, True),
        'gpbf': tail_rows(layout.gpbf_names, False),
        'failures': failures,
        'notes': notes,
    }
