"""Workbench材质接口末尾扩展；串行构造独立候选，全部检查后才提交内存事务。

仅支持已有UserMaterial的float1..4尾部参数与已有PS表的Texture2D。
调用方提供关联MDF的布局证明；未知IR、阶段或绑定不猜测，不接触游戏文件。
"""
import copy
import hashlib
import re
import struct

from inspect_mmtr import decode_dxbc, inspect_data, reflection
from mmtr_editor.hashes import murmur3_x86_32
from .material_addresses import MaterialTailLayout, material_reads, relocate_material_reads
from .model import Document, EditError, interface_chunks


# 拆分完整容器；只重建明确修改的chunk，STAT必须来自修改后的反射模块。
def chunks(blob):
    return {c['tag']: blob[c['offset']+8:c['offset']+8+c['size']]
            for c in decode_dxbc(blob, 0)['chunks']}


def replace_chunks(template, replacements):
    blocks = []
    for tag, data in chunks(template).items():
        data = replacements.get(tag, data)
        blocks.append(tag.encode('ascii') + struct.pack('<I', len(data)) + data)
    header = bytearray(template[:32])
    header[4:20] = bytes(16)
    cursor = 32 + len(blocks)*4
    struct.pack_into('<I', header, 28, len(blocks))
    for block in blocks:
        header.extend(struct.pack('<I', cursor))
        cursor += len(block)
    struct.pack_into('<I', header, 24, cursor)
    return bytes(header) + b''.join(blocks)


# 将STAT内的反射bitcode作为独立DXIL暴露给DXC，不把它当可执行程序运行。
def reflection_container(template):
    stat = chunks(template).get('STAT')
    if stat is None:
        raise EditError('Shader缺少STAT反射，不能自动扩展接口。')
    header = bytearray(template[:32])
    header[4:20] = bytes(16)
    struct.pack_into('<II', header, 24, 44+len(stat), 1)
    return bytes(header) + struct.pack('<I', 36) + b'DXIL' + struct.pack('<I', len(stat)) + stat


# 只在space0有限SRV尾部追加；其它space的有限/无界声明逐字保留，不参与寄存器分配。
# 调用方持有本次独立IR候选；不能把无界数组类型复制成新增的单纹理类型。
def append_texture_declaration(nodes, table, texture, node):
    entries = re.findall(r'!(\d+)', nodes[table])
    pattern = (r'!\{i32 (\d+), (.*?), !"[^"]*", i32 (\d+), i32 (\d+), '
               r'i32 (-?\d+), i32 (\d+), i32 (\d+), (.*)\}')
    parsed = [re.fullmatch(pattern, nodes[key]) for key in entries]
    if not parsed or not all(parsed):
        raise EditError('SRV声明结构无法识别，不能分配新增纹理寄存器。')
    identities, ranges, scalar_textures = set(), [], []
    for row in parsed:
        identity, space, start, count = (int(row[i]) for i in (1, 3, 4, 5))
        if identity in identities or identity > 0xffffffff or space > 0xffffffff or start > 0xffffffff:
            raise EditError('SRV身份或寄存器范围无效。')
        identities.add(identity)
        # LLVM可能把UINT_MAX数量显示为有符号-1；0及其它负数没有已验证语义。
        unbounded = count in (-1, 0xffffffff)
        if not unbounded and (count <= 0 or count > 0xffffffff or start + count > 0x100000000):
            raise EditError('SRV数量或寄存器范围无效。')
        if space != 0:
            continue
        if unbounded:
            raise EditError('space0包含无界SRV，不能证明新增纹理的有限寄存器范围。')
        ranges.append((start, start + count))
        if (count == 1 and row[6] == '2'
                and re.fullmatch(r'%"class\.Texture2D<vector<float, 4> >"\* undef', row[2])):
            scalar_textures.append(row)
    if not ranges or not scalar_textures:
        raise EditError('缺少space0中已识别的单个float4 Texture2D声明模板。')
    ordered = sorted(ranges)
    if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
        raise EditError('space0的SRV寄存器范围重叠。')
    register = max(end for _, end in ranges)
    identity = max(identities) + 1
    if register > 0xffff or identity > 0xffffffff:
        raise EditError('新增纹理寄存器或资源身份超出绑定表范围。')
    sample = scalar_textures[0]
    added = node(f'!{{i32 {identity}, {sample[2]}, !"{texture}", i32 0, i32 {register}, '
                 f'i32 1, i32 2, i32 0, {sample[8]}}}')
    nodes[table] = nodes[table][:-1] + f', !{added}}}'
    return register


# 修改声明、类型注解和资源尺寸，不向渲染算式加入新增字段的消费者。
def patch_declarations(ir, old_size, new_size, parameter=None, texture=None):
    nodes = dict(re.findall(r'^!(\d+) = (.*)$', ir, re.M))
    next_id = max(map(int, nodes), default=-1)+1
    extra = []

    def node(value):
        nonlocal next_id
        key = str(next_id)
        next_id += 1
        extra.append(f'!{key} = {value}')
        return key

    if parameter:
        width = parameter['byte_size']//4
        field_type = 'float' if width == 1 else f'<{width} x float>'
        ir, count = re.subn(r'^(%UserMaterial = type \{ .*?) \}$',
                            lambda m: m[1]+f', {field_type} }}', ir, flags=re.M)
        if count != 1:
            raise EditError('UserMaterial类型不是已支持的显式结构。')
        sizes, annotations = 0, 0
        for key, value in list(nodes.items()):
            if '%UserMaterial* undef' in value:
                marker = f', i32 {old_size}, null}}'
                if marker not in value:
                    raise EditError('UserMaterial资源尺寸与当前布局不符。')
                nodes[key] = value.replace(marker, f', i32 {new_size}, null}}')
                sizes += 1
            if '%UserMaterial undef, !' in value:
                annotation = re.search(r'%UserMaterial undef, !(\d+)', value)[1]
                marker = f'!{{i32 {old_size}, '
                if not nodes[annotation].startswith(marker):
                    raise EditError('UserMaterial反射类型尺寸不一致。')
                field = node(f'!{{i32 6, !"VAR_{parameter["name"]}", i32 3, '
                             f'i32 {parameter["byte_offset"]}, i32 7, i32 9}}')
                nodes[annotation] = nodes[annotation].replace(marker, f'!{{i32 {new_size}, ', 1)[:-1] + f', !{field}}}'
                annotations += 1
        # 可执行DXIL可能已剥离类型注解；STAT模块必须独立重建并在编译后核对变量。
        if sizes != 1 or annotations > 1:
            raise EditError('缺少唯一UserMaterial反射注解，不能扩展。')
        ir = ir.replace(f'%dx.types.ResourceProperties {{ i32 13, i32 {old_size} }}',
                        f'%dx.types.ResourceProperties {{ i32 13, i32 {new_size} }}')
    register = None
    if texture:
        root = re.search(r'^!dx.resources = !\{!(\d+)\}$', ir, re.M)
        srv = re.match(r'!\{!(\d+),', nodes[root[1]]) if root else None
        if not srv:
            raise EditError('Shader没有可扩展的SRV声明表。')
        register = append_texture_declaration(nodes, srv[1], texture, node)
    ir = re.sub(r'^!(\d+) = (.*)$', lambda m: f'!{m[1]} = {nodes[m[1]]}', ir, flags=re.M)
    return ir+'\n'+'\n'.join(extra)+'\n', register


# 正文比较允许经审核的地址/尺寸变化；拒绝DXC意外改变原计算和控制流。
def bodies(ir):
    text = '\n'.join(re.findall(r'^define .*?^}', ir, re.M | re.S))
    text = re.sub(r';[^\n]*', '', text)
    text = re.sub(r', ![A-Za-z.]+ !\d+', '', text)
    return [line.rstrip() for line in text.splitlines() if line.strip()]


# 只向前缀末尾追加描述符及绑定双表，原root/state等未知区域保持绝对地址。
def append_metadata(data, source, operation):
    _, info = inspect_data(data, source)
    prefix = bytearray(data[:info['shader_start']])

    def append(raw, alignment=8):
        prefix.extend(bytes((-len(prefix)) % alignment))
        at = len(prefix)
        prefix.extend(raw)
        return at

    parameter = operation.get('parameter')
    if parameter:
        entries = [cb for cb in info['metadata']['cbuffers'] if cb['name'] == 'UserMaterial']
        first = entries[0]
        name = parameter['name']
        name_at = append(name.encode('ascii')+b'\0', 1)
        members = data[first['members_offset']:first['members_offset']+first['member_count']*16]
        members += struct.pack('<QIHH', name_at, murmur3_x86_32(name.encode('ascii'), 0xffffffff),
                               parameter['byte_offset'], parameter['byte_size'])
        at = append(members)
        for cb in entries:
            struct.pack_into('<IIQ', prefix, cb['offset']+16, operation['new_size'], first['member_count']+1, at)
    texture = operation.get('texture')
    if texture:
        name_at = append(texture.encode('ascii')+b'\0', 1)
        for group in operation['tables']:
            records = [info['records'][i] for i in group['records']]
            offsets = records[0]['metadata_tables']['srvs']
            table = b''.join(data[pos:pos+16] for pos in offsets)
            table += struct.pack('<QII', name_at, murmur3_x86_32(texture.encode('ascii'), 0xffffffff), 0)
            table_at, binding_tables = append(table), {}
            for record in records:
                if record['metadata_tables']['srvs'] != offsets:
                    raise EditError('共享SRV名称表在事务中发生变化。')
                old_binding = struct.unpack_from('<Q', data, record['offset']+0x88)[0]
                register = group['registers'][str(record['index'])]
                key = (old_binding, register)
                if not 0 < old_binding < info['shader_start'] or old_binding+len(offsets)*8 > info['shader_start']:
                    raise EditError('原SRV绑定映射表越界。')
                if key not in binding_tables:
                    binding_tables[key] = append(data[old_binding:old_binding+len(offsets)*8]
                                                  + struct.pack('<II', 0, 0x02100000 | register), 4)
                base = record['offset']
                total = struct.unpack_from('<I', data, base+0xd4)[0]
                if total != sum(sum(x) for x in record['per_stage_counts_raw'].values()):
                    raise EditError('原MMTR全阶段资源总数不一致。')
                if prefix[base+0xfc] >= 255 or prefix[base+0xe8] >= 255:
                    raise EditError('SRV数量超出单字节范围。')
                struct.pack_into('<QQ', prefix, base+0x88, binding_tables[key], table_at)
                prefix[base+0xfc] += 1
                prefix[base+0xe8] += 1
                struct.pack_into('<I', prefix, base+0xd4, total+1)
    delta = len(prefix)-info['shader_start']
    struct.pack_into('<Q', prefix, 8, len(prefix))
    for record in info['records']:
        for reference in record['shader_references']:
            struct.pack_into('<Q', prefix, record['offset']+reference['field'], reference['offset']+delta)
    return bytes(prefix)+data[info['shader_start']:]


# 从关联MDF及既有扩展记录恢复当前尾部布局；不把缺反射当作没有材质数据。
def current_layout(document, material, info=None):
    info = info if info is not None else document.current_info()
    buffers = [cb for cb in info['metadata']['cbuffers'] if cb['name'] == 'UserMaterial']
    if not buffers or len({(cb['buffer_size'], cb['members_offset'], cb['member_count']) for cb in buffers}) != 1:
        raise EditError('UserMaterial共享成员布局不一致。')
    size = buffers[0]['buffer_size']
    textures = tuple(t['name'] for t in material['textures'])
    gpbf = tuple(g['name'] for g in material['gpbf'])
    extensions = [op for op in document.metadata_edits if op['kind'] == 'extend_material_interface']
    if extensions:
        saved = extensions[-1]
        expected = tuple(saved['textures'])
        if textures != expected[:len(textures)] or gpbf != tuple(saved['gpbf']):
            raise EditError('关联MDF的纹理/GPBF顺序与当前MMTR扩展布局不一致。')
        textures = expected
    else:
        if material['property_block_size'] != size:
            raise EditError('关联MDF参数区大小与UserMaterial不一致。')
        members = {m['name']: m for m in buffers[0]['members']}
        property_names = {p['name'] for p in material['properties']}
        missing = set(members)-property_names
        if property_names-set(members) or any(not re.fullmatch(r'CAPCOM_MATERIAL_RESERVE\d+', name) for name in missing):
            raise EditError('关联MDF参数与UserMaterial成员不一致，请先同步已有接口。')
        for prop in material['properties']:
            member = members[prop['name']]
            if prop['offset'] != member['byte_offset'] or prop['byte_size'] != member['byte_size']:
                raise EditError('关联MDF参数偏移/宽度与UserMaterial不一致。')
    if len(set(textures+gpbf)) != len(textures+gpbf):
        raise EditError('材质尾部资源名称重复，无法证明字段身份。')
    return MaterialTailLayout(size, textures, gpbf)


# 选择所有使用当前材质纹理的共享表，闭合PS引用；不创建未知阶段或空表。
def texture_tables(document, layout, record_index):
    info = document.current_info()
    if not 0 <= record_index < len(info['records']):
        raise EditError('MMTR记录索引无效。')
    names = {e['offset']: e['name'] for e in info['metadata']['srvs']}
    selected_names = {names[x] for x in info['records'][record_index]['metadata_tables']['srvs']}
    material_names = set(layout.texture_names).intersection(selected_names)
    if not material_names:
        raise EditError('所选记录未使用关联MDF纹理表，请选择材质绘制记录。')
    tables, shaders = {}, set()
    for record in info['records']:
        offsets = tuple(record['metadata_tables']['srvs'])
        if not material_names.issubset({names[x] for x in offsets}):
            continue
        if record['per_stage_counts_raw']['srvs'][5] or not any(ref['stage'] == 'ps' for ref in record['shader_references']):
            raise EditError('材质纹理表包含未支持的计算阶段或没有PS。')
        tables.setdefault(offsets, []).append(record['index'])
        shaders.update(ref['shader_index'] for ref in record['shader_references'] if ref['stage'] == 'ps')
    if not any(record_index in records for records in tables.values()):
        raise EditError('所选记录未使用关联MDF纹理表，请选择材质绘制记录。')
    selected = {i for records in tables.values() for i in records}
    for record in info['records']:
        if any(ref['shader_index'] in shaders for ref in record['shader_references']) and record['index'] not in selected:
            raise EditError('材质PS被其它未识别资源表共享，不能局部扩展。')
    return tables, shaders


# 一次新增一种接口；任何编译、映射或完整性检查失败都保留调用方原状态。
def extend_material_interface(document, material, dll, *, parameter=None, texture=None, record_index=None):
    from .dxil_assembly import DxilAssembly
    if bool(parameter) == bool(texture):
        raise EditError('一次只能新增一个参数或纹理接口。')
    name = parameter['name'] if parameter else texture
    if not re.fullmatch(r'[A-Za-z_]\w*', name, re.ASCII):
        raise EditError('新增接口名称必须是ASCII标识符。')
    if any(op['kind'] != 'extend_material_interface' for op in document.metadata_edits):
        raise EditError('请先完成或重置已有手动metadata修改，再自动扩展材质接口。')
    old = current_layout(document, material)
    info = document.current_info()
    original_data = document.build()[0]
    tables, texture_shaders = {}, set()
    new_size = old.parameter_bytes
    if parameter:
        if parameter['byte_size'] not in (4, 8, 12, 16) or parameter['byte_offset'] != old.parameter_bytes:
            raise EditError('自动扩展支持4/8/12/16字节参数，偏移必须等于当前UserMaterial大小。')
        if any(m['name'] == name for cb in info['metadata']['cbuffers'] if cb['name'] == 'UserMaterial' for m in cb['members']):
            raise EditError(f'MMTR参数已存在：{name}')
        new_size = (parameter['byte_offset']+parameter['byte_size']+15) & ~15
        if new_size > 0xffff:
            raise EditError('UserMaterial超过16位布局范围。')
    else:
        if name in old.texture_names+old.gpbf_names or any(e['name'] == name for e in info['metadata']['srvs']):
            raise EditError(f'MMTR资源已存在：{name}')
        tables, texture_shaders = texture_tables(document, old, record_index)
    new = MaterialTailLayout(new_size, old.texture_names+((texture,) if texture else ()), old.gpbf_names)
    candidate = copy.deepcopy(document)
    reports, registers, reflections = [], {}, {}
    compiler = DxilAssembly(dll)
    try:
        for shader in info['shaders']:
            index = shader['index']
            original = document.shader_bytes(index)
            ir = compiler.disassemble(original)
            before = reflection(ir)
            reflections[index] = hashlib.sha256(original).digest(), before
            has_cb = parameter and any(cb['name'] == 'UserMaterial' for cb in before['cbuffers'])
            has_texture = index in texture_shaders
            # 未修改声明的Shader保持原文本；同一源只解析一次供计划和编译后对照使用。
            edited, register = (patch_declarations(ir, old.parameter_bytes, new_size,
                                                  parameter if has_cb else None, texture if has_texture else None)
                                if has_cb or has_texture else (ir, None))
            reads = material_reads(ir)
            edited, relocation = relocate_material_reads(edited, old, new, reads=reads)
            if not has_cb and not has_texture and not relocation['relocated_extracts'] and not (
                    relocation['instance_stride_ssa'] and old.parameter_bytes != new_size):
                continue
            reflected_ir = compiler.disassemble(reflection_container(original))
            reflected_ir, reflected_register = patch_declarations(reflected_ir, old.parameter_bytes, new_size,
                                                                  parameter if has_cb else None, texture if has_texture else None)
            if reflected_register != register:
                raise EditError(f'Shader #{index} 的可执行/反射寄存器不一致。')
            assembled, _ = compiler.assemble(edited)
            reflected, _ = compiler.assemble(reflected_ir)
            payload, _ = compiler.validate(replace_chunks(assembled, {'STAT': chunks(reflected)['STAT']}))
            actual = compiler.disassemble(payload)
            after = reflection(actual)
            reflections[index] = hashlib.sha256(payload).digest(), after
            if bodies(actual) != bodies(edited) or interface_chunks(original) != interface_chunks(payload):
                raise EditError(f'Shader #{index} 的原计算/签名出现意外变化。')
            previous_reads = {ex['ssa']: ex['offset'] for r in reads if r['kind'] != 'instance' for ex in r['extracts']}
            compiled_reads = material_reads(actual)
            actual_reads = {ex['ssa']: ex['offset'] for r in compiled_reads if r['kind'] != 'instance' for ex in r['extracts']}
            if any(actual_reads.get(ssa) != old.offset_in(offset, new) for ssa, offset in previous_reads.items()):
                raise EditError(f'Shader #{index} 编译后材质字段读取不一致。')
            if any(r['stride']['bytes'] != new_size for r in compiled_reads if r['kind'] == 'instance'):
                raise EditError('编译后参数实例步长不一致。')
            if before['structured_buffers'] != after['structured_buffers']:
                raise EditError('结构化缓冲反射发生意外变化。')
            added_bindings = [row for row in after['resource_binding_rows'] if row not in before['resource_binding_rows']]
            if (any(row not in after['resource_binding_rows'] for row in before['resource_binding_rows'])
                    or len(added_bindings) != int(has_texture)):
                raise EditError('原资源绑定反射发生意外变化。')
            if has_texture and (added_bindings[0].split()[0] != texture or added_bindings[0].split()[-2] != f't{register}'):
                raise EditError('新增纹理反射与绑定寄存器不一致。')
            for cb in before['cbuffers']:
                updated = next((b for b in after['cbuffers'] if b['name'] == cb['name']), None)
                if has_cb and cb['name'] == 'UserMaterial':
                    if (updated is None or updated['size'] != new_size or updated['variables'][:-1] != cb['variables']
                            or updated['variables'][-1]['name'] != 'VAR_'+name
                            or updated['variables'][-1]['offset'] != parameter['byte_offset']
                            or Document._reflection_member_size(updated['variables'][-1]['type']) != parameter['byte_size']):
                        raise EditError('新增参数反射布局不符合预期。')
                elif updated != cb:
                    raise EditError('原常量缓冲反射发生意外变化。')
            candidate.replace_shader(index, payload, allow_interface_change=True,
                                     note={'material_extension': True, 'dxc_validated': True})
            registers[index] = register
            reports.append({'index': index, 'relocated_reads': len(relocation['relocated_extracts']),
                            'instance_strides': len(relocation['instance_stride_ssa'])})
    finally:
        compiler.close()
    operation = dict(kind='extend_material_interface', parameter=parameter, texture=texture, new_size=new_size,
                     textures=list(new.texture_names), gpbf=list(new.gpbf_names), tables=[])
    for offsets, records in tables.items():
        by_record = {}
        for index in records:
            used = {registers[ref['shader_index']] for ref in info['records'][index]['shader_references'] if ref['stage'] == 'ps'}
            if len(used) != 1:
                raise EditError('共享记录的新增纹理寄存器不唯一。')
            by_record[str(index)] = used.pop()
        operation['tables'].append({'records': records, 'registers': by_record})
    operation['shader_hashes'] = {str(s['index']): hashlib.sha256(candidate.shader_bytes(s['index'])).hexdigest()
                                  for s in info['shaders']}
    operation['redirects'] = [[record, field, target] for (record, field), target in sorted(candidate.redirects.items())]
    candidate.metadata_edits.append(operation)
    rebuilt, _ = candidate.build()
    reopened = Document(rebuilt, document.source)
    if reopened.integrity['name_hash_mismatches'] or reopened.integrity['shader_trailer_mismatches']:
        raise EditError('扩展后的MMTR完整性检查失败。')
    affected = {i for group in operation['tables'] for i in group['records']}
    for record in reopened.info['records']:
        original = info['records'][record['index']]
        base = record['offset']
        if struct.unpack_from('<I', rebuilt, base+0xd4)[0] != sum(sum(v) for v in record['per_stage_counts_raw'].values()):
            raise EditError('扩展后的全阶段资源总数不一致。')
        if record['index'] in affected:
            count = len(original['metadata_tables']['srvs'])
            old_pointer = struct.unpack_from('<Q', original_data, original['offset']+0x88)[0]
            pointer = struct.unpack_from('<Q', rebuilt, base+0x88)[0]
            if rebuilt[pointer:pointer+count*8] != original_data[old_pointer:old_pointer+count*8]:
                raise EditError('原SRV绑定映射被改变。')
    validation = candidate.validate_metadata_interfaces(dll, _in_worker=True, _reflections=reflections)
    if not validation['valid']:
        raise EditError(f'新增接口验证失败：{validation["issues"][0]}')
    document.replacements, document.replacement_notes = candidate.replacements, candidate.replacement_notes
    document.metadata_edits = candidate.metadata_edits
    return {'changed_shaders': len(reports), 'shader_reports': reports, 'srv_records': len(affected),
            'parameter_bytes': new_size, 'validation': validation}
