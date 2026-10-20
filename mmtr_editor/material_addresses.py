"""有界材质布局迁移：只支持已识别的 CBV、16B BindlessBuffer 和参数实例读取。

调用方显式提供旧/新布局并持有原 IR；此模块只返回独立文本，不管理 DXC 或游戏资源。
未知地址、aggregate 消费或布局末尾外读取均拒绝，不根据反射缺失推断未受影响。
"""
from dataclasses import dataclass
import re

from .dxil_sources import parse, handle_sources


# 布局描述参数区、纹理索引区和 GPBF 索引区；本次只允许末尾追加，不移动既有参数。
@dataclass(frozen=True)
class MaterialTailLayout:
    parameter_bytes: int
    texture_names: tuple[str, ...]
    gpbf_names: tuple[str, ...]

    def fields(self):
        return {name: self.parameter_bytes + index * 4
                for index, name in enumerate(self.texture_names + self.gpbf_names)}

    def offset_in(self, offset, target):
        if offset < 0 or offset % 4:
            raise ValueError('材质地址必须是非负的4字节对齐值。')
        if offset < self.parameter_bytes:
            return offset
        old_fields, new_fields = self.fields(), target.fields()
        name = next((key for key, value in old_fields.items() if value == offset), None)
        if name is None or name not in new_fields:
            raise ValueError(f'无法证明材质尾部偏移 {offset} 的资源身份。')
        return new_fields[name]


# 解析来源明确的三类读取；只有通过材质表/heap-CBV构造证明身份的句柄进入返回值。
def material_reads(ir):
    definitions, uses, _ = parse(ir)
    origins = handle_sources(ir, definitions)
    for row in definitions.values():
        if not row['text'].startswith('call %dx.types.Handle @dx.op.annotateHandle('):
            continue
        annotated = re.fullmatch(
            r'call %dx.types.Handle @dx.op.annotateHandle\(i32 216, %dx.types.Handle (%\w+), '
            r'%dx.types.ResourceProperties \{ i32 13, i32 (\d+) \}\)', row['text'])
        if (annotated and '@dx.op.createHandleFromHeap' in definitions.get(annotated[1], {}).get('text', '')
                and int(annotated[2]) != 32768):
            raise ValueError('heap常量缓冲尺寸不是已验证的32768B材质布局。')
    handles = {key: 'cb' for key, row in definitions.items()
               if row['text'].startswith('call %dx.types.Handle @dx.op.annotateHandle(') and re.fullmatch(
        r'call %dx.types.Handle @dx.op.annotateHandle\(i32 216, %dx.types.Handle %\w+, '
        r'%dx.types.ResourceProperties \{ i32 13, i32 32768 \}\)', row['text'])}
    for key in handles:
        parent = re.search(r'%dx.types.Handle (%\w+), %dx.types.ResourceProperties', definitions[key]['text'])[1]
        if '@dx.op.createHandleFromHeap' not in definitions.get(parent, {}).get('text', ''):
            raise ValueError('32768B常量缓冲不是已识别的heap材质句柄。')
    for key, origin in origins.items():
        name = origin.get('binding', {}).get('name')
        if name in ('BindlessBuffer', 'UserMaterialInstances'):
            handles[key] = 'raw' if name == 'BindlessBuffer' else 'instance'
    result = []
    for value, row in definitions.items():
        text = row['text']
        if not any(op in text for op in ('@dx.op.cbufferLoad', '@dx.op.rawBufferLoad', '@dx.op.bufferLoad')):
            continue
        hm = re.search(r'%dx.types.Handle (%\w+)', text)
        if not hm or hm[1] not in handles:
            continue
        kind = handles[hm[1]]
        register, base, stride = None, None, None
        # 自定义Shader可直接按float读取同一材质CB；地址证明和字段迁移与整数读取相同。
        cb = re.fullmatch(r'call %dx\.types\.CBufRet\.(i32|f32) @dx\.op\.cbufferLoadLegacy\.\1\(i32 59, %dx.types.Handle %\w+, i32 (\d+)\)', text)
        raw = re.fullmatch(r'call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32\(i32 139, %dx.types.Handle %\w+, i32 (%\w+), i32 (0|undef), i8 (\d+), i32 4\)', text)
        if cb and kind == 'cb':
            register = int(cb[2])
        elif raw:
            address = definitions[raw[1]]['text']
            add = re.fullmatch(r'(?:add(?: nuw)?(?: nsw)?|or) i32 (%\w+), (\d+)', address)
            base, relative = (add[1], int(add[2])) if add else (raw[1], 0)
            if kind == 'raw' and raw[2] == '0':
                if not re.fullmatch(r'lshr i32 %\w+, 4', definitions[base]['text']):
                    raise ValueError(f'未知材质表基址：{address}')
                register = relative
            elif kind == 'instance' and raw[2] == 'undef':
                stride = re.fullmatch(r'mul(?: nuw)?(?: nsw)? i32 (%\w+), (\d+)', definitions[base]['text'])
                if not stride or relative % 16:
                    raise ValueError(f'未知参数实例地址：{address}')
                stride = {'ssa': base, 'index': stride[1], 'bytes': int(stride[2])}
                if address.startswith('or ') and relative >= (stride['bytes'] & -stride['bytes']):
                    raise ValueError('实例OR地址未证明与加法等价。')
                register = relative // 16
        if register is None:
            raise ValueError(f'未知材质读取：{text}')
        extracts = []
        aggregate = 'CBufRet.' + cb[1] if cb else 'ResRet.i32'
        for user in uses[value]:
            ex = re.fullmatch(r'extractvalue %dx.types.' + re.escape(aggregate) + ' ' + re.escape(value) + r', ([0-3])', user['text'])
            if not ex:
                raise ValueError(f'材质aggregate有未支持的消费者：{user["text"]}')
            extracts.append({'ssa': user['value'], 'lane': int(ex[1]), 'offset': register * 16 + int(ex[1]) * 4})
        result.append({'ssa': value, 'text': text, 'kind': kind, 'register': register,
                       'base': base, 'stride': stride, 'address_ssa': raw[1] if raw else None,
                       'address_rhs': definitions[raw[1]]['text'] if raw else None,
                       'raw': raw.groups() if raw else None,
                       'extracts': extracts})
    return result


# 仅重写经来源证明的读取和参数实例步长；保留所有原计算/控制流，用新命名SSA避免重排编号。
def relocate_material_reads(ir, old, new, *, reads=None):
    if old.parameter_bytes % 16 or new.parameter_bytes % 16 or new.parameter_bytes < old.parameter_bytes:
        raise ValueError('只接受16字节对齐的参数区末尾扩容。')
    reads = material_reads(ir) if reads is None else reads
    replacements, changes, strides = {}, [], {}
    # 每个SSA只索引一次，按行构造候选；不为每处迁移全文搜索/替换。
    lines = ir.splitlines(keepends=True)
    anchors = {}
    for line_index, line in enumerate(lines):
        match = re.match(r'^  (%\w+) = ', line)
        if match:
            anchors.setdefault(match[1], []).append(line_index)

    def anchor(ssa):
        indexes = anchors.get(ssa, [])
        if len(indexes) != 1:
            raise ValueError('IR替换锚点不唯一。')
        index = indexes[0]
        if not lines[index].endswith('\n'):
            raise ValueError('IR替换锚点缺少换行。')
        return index, lines[index].rstrip('\r\n')

    # 同一会话可连续追加纹理和参数；前一次生成的SSA仍在IR中，后续必须分配新名称。
    used_names = set(re.findall(r'^\s*(%\w+)\s*=', ir, re.M))
    for read in reads:
        if read['kind'] == 'instance':
            stride = read['stride']
            if stride['bytes'] != old.parameter_bytes:
                raise ValueError('实例步长与原参数区尺寸不一致。')
            strides[stride['ssa']] = stride
            if read['address_rhs'].startswith('or '):
                address = read['address_ssa']
                line_index, _ = anchor(address)
                replacements[line_index] = f'  {address} = ' + read['address_rhs'].replace('or ', 'add ', 1)
        wanted = {}
        for ex in read['extracts']:
            if read['kind'] == 'instance':
                if ex['offset'] >= old.parameter_bytes:
                    raise ValueError('参数实例流出现尾部资源读取，需单独证明其步长。')
                continue
            target = old.offset_in(ex['offset'], new)
            if target != ex['offset']:
                wanted[ex['ssa']] = (ex, target)
        if not wanted:
            continue
        groups = sorted({target // 16 for _, target in wanted.values()})
        additions, group_names = [], {}
        for reg in groups:
            stem = f'%mmtrReloc{read["ssa"][1:]}r{reg}'
            name, suffix = stem, 0
            while name in used_names or name + 'addr' in used_names:
                suffix += 1
                name = f'{stem}_{suffix}'
            used_names.update((name, name + 'addr'))
            group_names[reg] = name
            if read['kind'] == 'cb':
                text = re.sub(r'i32 \d+\)$', f'i32 {reg})', read['text'])
            else:
                address = name + 'addr'
                additions.append(f'  {address} = add i32 {read["base"]}, {reg}')
                text = read['text'].replace(f'i32 {read["raw"][0]},', f'i32 {address},')
            additions.append(f'  {name} = {text}')
        line_index, line = anchor(read['ssa'])
        replacements[line_index] = line + '\n' + '\n'.join(additions)
        for ssa, (ex, target) in wanted.items():
            pattern = r'^  ' + re.escape(ssa) + r' = extractvalue (.*) ' + re.escape(read['ssa']) + r', [0-3](?:  ;.*)?$'
            line_index, line = anchor(ssa)
            match = re.fullmatch(pattern.removeprefix('^').removesuffix('$'), line)
            if not match:
                raise ValueError('IR取值锚点与已解析的材质读取不一致。')
            replacements[line_index] = f'  {ssa} = extractvalue {match[1]} {group_names[target//16]}, {target%16//4}'
            changes.append({'ssa': ssa, 'kind': read['kind'], 'old_offset': ex['offset'], 'new_offset': target})
    for ssa, stride in strides.items():
        line_index, line = anchor(ssa)
        rhs = line.split(' = ', 1)[1]
        updated = re.sub(r', ' + str(old.parameter_bytes) + r'$', f', {new.parameter_bytes}', rhs)
        replacements[line_index] = f'  {ssa} = {updated}'
    if not replacements:
        return ir, {'loads': len(reads), 'relocated_extracts': changes,
                    'instance_stride_ssa': sorted(strides), 'new_stride': new.parameter_bytes}
    ir = ''.join(replacements[i] + '\n' if i in replacements else line for i, line in enumerate(lines))
    # 独立解析迁移后的取值源，不以计划替代实际文本。
    before = {ex['ssa']: ex['offset'] for read in reads if read['kind'] != 'instance' for ex in read['extracts']}
    after_reads = material_reads(ir)
    after = {ex['ssa']: ex['offset'] for read in after_reads if read['kind'] != 'instance' for ex in read['extracts']}
    if any(after.get(ssa) != old.offset_in(offset, new) for ssa, offset in before.items()):
        raise ValueError('实际IR中的原取值SSA没有迁移到预期逻辑字段。')
    return ir, {'loads': len(reads), 'relocated_extracts': changes,
                'instance_stride_ssa': sorted(strides), 'new_stride': new.parameter_bytes}
