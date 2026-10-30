"""有界识别新版材质布局标记；MDF 计数、尾部位置和唯一比较消费者必须相互印证。

标记不是纹理/GPBF 槽，不放宽普通地址迁移的越界拒绝。只改独立候选文本。
"""
import re

from .dxil_sources import ensure_graph
from .model import EditError


def marker_value(layout, components):
    """编码只用于核对已有比较值；不从 MMTR 成员数推断 MDF 标量数。"""
    if not (0 <= components < 1024 and len(layout.texture_names) < 256
            and len(layout.gpbf_names) < 16384):
        raise EditError('材质标记计数超出已验证的位域范围。')
    return components | (len(layout.texture_names) << 10) | (len(layout.gpbf_names) << 18)


def marker_offset(layout):
    """荒野与 RE4 的标记在尾部之后的下一行；鬼武者的标记在参数区之前的第 0 行（偏移为负）。"""
    if layout.header_bytes:
        return -layout.header_bytes
    return (layout.parameter_bytes + 4*len(layout.texture_names+layout.gpbf_names) + 15) & ~15


def material_marker_reads(ir, layout, components, reads, *, graph=None):
    """分离已证明的标记读取；其余越界读取继续由普通迁移拒绝。"""
    uses = ensure_graph(ir, graph).uses
    ordinary, guards = [], []
    end = layout.parameter_bytes + 4*len(layout.texture_names+layout.gpbf_names)
    for read in reads:
        outside = [ex for ex in read['extracts'] if ex['offset'] >= end or ex['offset'] < 0]
        if not outside or read['kind'] not in ('raw', 'cb'):
            ordinary.append(read)
            continue
        if (len(read['extracts']) != 1 or outside[0]['lane'] != 0
                or outside[0]['offset'] != marker_offset(layout)):
            raise EditError('未知材质尾部读取不是已验证的布局标记。')
        extract = outside[0]['ssa']
        consumers = uses.get(extract, [])
        value = marker_value(layout, components)
        if len(consumers) != 1 or consumers[0]['text'] not in (
                f'icmp eq i32 {extract}, {value}', f'icmp ne i32 {extract}, {value}'):
            raise EditError('材质布局标记的比较值/消费者与 MDF 计数不一致。')
        guards.append({'read': read, 'extract': extract, 'comparison': consumers[0]['value'],
                       'text': consumers[0]['text']})
    return ordinary, guards


def relocate_material_markers(ir, old, new, components, added_components, reads, *, graph=None):
    """保留标记检查；独立新地址避免改变共享地址 SSA 的其它消费者。"""
    ordinary, guards = material_marker_reads(ir, old, components, reads, graph=graph)
    if not guards:
        return ir, ordinary, guards
    old_offset, new_offset = marker_offset(old), marker_offset(new)
    old_value, new_value = marker_value(old, components), marker_value(new, components+added_components)
    lines = ir.splitlines(keepends=True)
    anchors, used = {}, set()
    for index, line in enumerate(lines):
        match = re.match(r'^  (%\w+) = ', line)
        if match:
            anchors.setdefault(match[1], []).append(index)
            used.add(match[1])

    def anchor(ssa):
        indexes = anchors.get(ssa, [])
        if len(indexes) != 1 or not lines[indexes[0]].endswith('\n'):
            raise EditError('材质标记 IR 锚点不唯一或缺少换行。')
        return indexes[0]

    for guard in guards:
        read = guard['read']
        if old_offset != new_offset:
            load = read['text']
            prefix = ''
            if read['kind'] == 'cb':
                load = re.sub(r'i32 \d+\)$', f'i32 {new_offset//16})', load)
            else:
                stem = '%mmtrGuard'+read['ssa'][1:]
                name, suffix = stem, 0
                while name in used:
                    suffix += 1
                    name = f'{stem}_{suffix}'
                used.add(name)
                prefix = f'  {name} = add i32 {read["base"]}, {new_offset//16}\n'
                load = load.replace(f'i32 {read["raw"][0]},', f'i32 {name},')
            lines[anchor(read['ssa'])] = prefix+f'  {read["ssa"]} = {load}\n'
        if old_value != new_value:
            comparison = guard['text'].rsplit(', ', 1)[0]+f', {new_value}'
            lines[anchor(guard['comparison'])] = f'  {guard["comparison"]} = {comparison}\n'
    return ''.join(lines), ordinary, guards
