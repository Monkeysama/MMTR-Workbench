"""有界删除的声明及 MMTR 表写回；只接收已证明布局，不持有 DXC 或会话。

前缀只追加替代表，保留未知区段与旧资源寄存器；调用方必须重编译、重解析并验证候选。
"""
import re
import struct

from inspect_mmtr import inspect_data
from .material_extension import patch_material_properties
from .model import EditError


def patch_removal_declarations(ir, layout, reference):
    """同步可执行/STAT 模块；用反射身份识别剥名声明，资源范围编号可变而寄存器不变。"""
    nodes = dict(re.findall(r'^!(\d+) = (.*)$', ir, re.M))
    root = re.search(r'^!dx.resources = !\{!(\d+)\}$', ir, re.M)
    if not root:
        if reference['cbuffers'] or reference['resource_binding_rows']:
            raise EditError('Shader 缺少可解释的资源根节点。')
        return ir
    resource_root = root[1]
    categories = re.fullmatch(r'!\{(null|!\d+), (null|!\d+), (null|!\d+), (null|!\d+)\}', nodes[resource_root])
    if not categories:
        raise EditError('资源分类表无法完整解析。')
    tables = list(categories.groups())
    if layout.removed_parameters and any(cb['name'] == 'UserMaterial' for cb in reference['cbuffers']):
        cb = next(cb for cb in reference['cbuffers'] if cb['name'] == 'UserMaterial')
        if cb['size'] != layout.old.parameter_bytes:
            raise EditError('UserMaterial 声明尺寸与源布局不一致。')
        expected = {p.name: p for p in layout.parameters}
        variables = cb['variables']
        if set(v['name'].removeprefix('VAR_') for v in variables) != set(expected) | set(layout.removed_parameters):
            raise EditError('UserMaterial 声明与 MDF 参数身份不完全一致。')
        type_match = re.search(r'^%UserMaterial = type \{ (.*?) \}$', ir, re.M)
        fields = type_match[1].split(', ') if type_match else []
        if len(fields) != len(variables):
            raise EditError('UserMaterial 不是已支持的显式 float1..4 结构。')
        remaining_types = []
        for field, variable in zip(fields, variables):
            name = variable['name'].removeprefix('VAR_')
            width = {'float': 4, '<2 x float>': 8, '<3 x float>': 12, '<4 x float>': 16}.get(field)
            if width is None:
                raise EditError('UserMaterial 包含未支持类型。')
            if name in expected:
                move = expected[name]
                if variable['offset'] != move.old_offset or width != move.byte_size:
                    raise EditError('UserMaterial 字段类型或原偏移不一致。')
                remaining_types.append(field)
        ir = ir[:type_match.start()]+f'%UserMaterial = type {{ {", ".join(remaining_types)} }}'+ir[type_match.end():]
        sizes = 0
        for key, value in list(nodes.items()):
            if '%UserMaterial* undef' in value:
                marker = f', i32 {layout.old.parameter_bytes}, null}}'
                if marker not in value:
                    raise EditError('UserMaterial 声明尺寸未知。')
                nodes[key] = value.replace(marker, f', i32 {layout.new.parameter_bytes}, null}}')
                sizes += 1
            if '%UserMaterial undef, !' in value:
                annotation = re.search(r'%UserMaterial undef, !(\d+)', value)[1]
                match = re.fullmatch(r'!\{i32 (\d+)((?:, !\d+)*)\}', nodes[annotation])
                keys = re.findall(r'!(\d+)', match[2]) if match else []
                if not match or int(match[1]) != layout.old.parameter_bytes or len(keys) != len(variables):
                    raise EditError('UserMaterial 类型注解布局未知。')
                kept = []
                for field_key, variable in zip(keys, variables):
                    name = variable['name'].removeprefix('VAR_')
                    field = nodes[field_key]
                    if f'!"{variable["name"]}"' not in field or f'i32 3, i32 {variable["offset"]},' not in field:
                        raise EditError('UserMaterial 字段反射注解身份未知。')
                    if name in expected:
                        nodes[field_key] = field.replace(f'i32 3, i32 {variable["offset"]},',
                                                       f'i32 3, i32 {expected[name].new_offset},', 1)
                        kept.append(field_key)
                nodes[annotation] = f'!{{i32 {layout.new.parameter_bytes}'+''.join(', !'+k for k in kept)+'}'
        if sizes != 1:
            raise EditError('UserMaterial 缺少唯一资源声明。')
        # typed getelementptr 需要另一套结构索引迁移，本档不猜测。
        if re.search(r'^  .*%UserMaterial', ir, re.M):
            raise EditError('UserMaterial 存在未覆盖的类型指针消费者。')
        ir = patch_material_properties(ir, layout.old.parameter_bytes, layout.new.parameter_bytes)
    if layout.removed_textures and tables[0] != 'null':
        key = tables[0][1:]
        entries = re.findall(r'!(\d+)', nodes[key])
        removed, mapping, kept = set(), {}, []
        bindings = {}
        for row in reference['resource_binding_rows']:
            columns = row.split()
            if len(columns) >= 7 and columns[1] == 'texture':
                match = re.fullmatch(r'T(\d+)', columns[-3])
                if match:
                    bindings[int(match[1])] = columns[0]
        for field_key in entries:
            value = nodes[field_key]
            row = re.fullmatch(r'!\{i32 (\d+), (.*?), !"([^"]*)", i32 (\d+), i32 (\d+), i32 (-?\d+), (.*)\}', value)
            if not row or int(row[1]) in mapping or int(row[1]) in removed:
                raise EditError('SRV 声明身份无效或重复。')
            identity = int(row[1])
            name = bindings.get(identity, row[3])
            if row[3] and row[3] != name:
                raise EditError('可执行/STAT 资源身份不一致。')
            if name in layout.removed_textures:
                if (row[4] != '0' or row[6] != '1' or not row[7].startswith('i32 2, i32 0,')
                        or row[2] != '%"class.Texture2D<vector<float, 4> >"* undef'):
                    raise EditError('删除仅支持 space0 单个 float4 Texture2D。')
                removed.add(identity)
            else:
                mapping[identity] = len(kept)
                nodes[field_key] = value.replace(f'!{{i32 {identity},', f'!{{i32 {len(kept)},', 1)
                kept.append(field_key)
        if removed:
            nodes[key] = '!{'+', '.join('!'+k for k in kept)+'}'
            if not kept:
                tables[0] = 'null'
            pattern = r'(@dx.op.createHandle\(i32 57, i8 0, i32 )(\d+)(,)'
            def legacy(match):
                identity = int(match[2])
                if identity not in mapping:
                    raise EditError('已删除 SRV 仍存在可执行句柄声明。')
                return match[1]+str(mapping[identity])+match[3]
            ir = re.sub(pattern, legacy, ir)
        nodes[resource_root] = '!{'+', '.join(tables)+'}'
    return re.sub(r'^!(\d+) = (.*)$', lambda m: f'!{m[1]} = {nodes[m[1]]}', ir, flags=re.M)


def remove_metadata(data, source, operation):
    """前缀末尾复制保留成员/绑定，不覆盖共享区段；同步全部已知引用记录及阶段计数。"""
    _, info = inspect_data(data, source)
    layout = operation['layout']
    prefix = bytearray(data[:info['shader_start']])
    def append(blob, alignment=8):
        prefix.extend(bytes((-len(prefix)) % alignment))
        pointer = len(prefix)
        prefix.extend(blob)
        return pointer
    if layout['removed_parameters']:
        entries = [cb for cb in info['metadata']['cbuffers'] if cb['name'] == 'UserMaterial']
        first = entries[0]
        if any((cb['members_offset'], cb['member_count'], cb['buffer_size']) !=
               (first['members_offset'], first['member_count'], layout['old']['parameter_bytes']) for cb in entries):
            raise EditError('UserMaterial 成员共享关系或源尺寸改变。')
        moves = {p['name']: p for p in layout['parameters']}
        members = bytearray()
        for member in first['members']:
            name = member['name']
            if name in moves:
                move = moves[name]
                if (member['byte_offset'], member['byte_size']) != (move['old_offset'], move['byte_size']):
                    raise EditError('MMTR 成员源身份改变。')
                row = bytearray(data[member['offset']:member['offset']+16])
                struct.pack_into('<H', row, 12, move['new_offset'])
                members.extend(row)
            elif name not in layout['removed_parameters']:
                raise EditError('MMTR 包含未纳入删除计划的成员。')
        pointer = append(members)
        for cb in entries:
            struct.pack_into('<IIQ', prefix, cb['offset']+16, layout['new']['parameter_bytes'], len(moves), pointer)
    names = {entry['offset']: entry['name'] for entry in info['metadata']['srvs']}
    removed = set(layout['removed_textures'])
    cache = {}
    for record in info['records']:
        offsets = record['metadata_tables']['srvs']
        selected = [i for i, pos in enumerate(offsets) if names[pos] in removed]
        if not selected:
            continue
        base = record['offset']
        binding = struct.unpack_from('<Q', data, base+0x88)[0]
        if not 0 < binding <= info['shader_start']-len(offsets)*8:
            raise EditError('SRV 绑定表越界。')
        key = tuple(offsets), binding
        if key not in cache:
            kept = [i for i in range(len(offsets)) if i not in selected]
            metadata = b''.join(data[offsets[i]:offsets[i]+16] for i in kept)
            raw_bindings = b''.join(data[binding+i*8:binding+(i+1)*8] for i in kept)
            cache[key] = (append(raw_bindings, 4) if kept else 0, append(metadata) if kept else 0)
        counts = list(record['per_stage_counts_raw']['srvs'])
        for i in selected:
            row = data[binding+i*8:binding+(i+1)*8]
            if row[6] & ~63 or row[7] != 2:
                raise EditError('待删除 SRV 绑定 mask 或资源类型未验证。')
            for stage in range(6):
                counts[stage] -= bool(row[6] & (1 << stage))
        if any(c < 0 for c in counts):
            raise EditError('删除后 SRV 分阶段计数为负。')
        struct.pack_into('<QQ', prefix, base+0x88, *cache[key])
        prefix[base+0xFC] -= len(selected)
        prefix[base+0xE4:base+0xEA] = bytes(counts)
        total = sum(sum(values) for kind, values in record['per_stage_counts_raw'].items() if kind != 'srvs')+sum(counts)
        struct.pack_into('<I', prefix, base+0xD4, total)
    delta = len(prefix)-info['shader_start']
    struct.pack_into('<Q', prefix, 8, len(prefix))
    for record in info['records']:
        for reference in record['shader_references']:
            struct.pack_into('<Q', prefix, record['offset']+reference['field'], reference['offset']+delta)
    return bytes(prefix)+data[info['shader_start']:]
