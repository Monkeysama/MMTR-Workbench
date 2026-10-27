"""证明原生动态 Texture2D 的索引来自 MDF 尾部槽；不改写句柄或猜测寄存器。

仅接受无界具名 Texture2D 数组或已注解的资源堆 Texture2D，以及直接/加零索引。
参数、GPBF、未知算术及同一 Shader 内固定/动态材质纹理混用均拒绝。
"""
import re

from .dxil_sources import handle_sources, parse
from .model import EditError


# 返回原有消费者与逻辑纹理身份，迁移后须按新布局得到同一组证明。
def native_texture_reads(ir, layout, reads, reflected):
    definitions, _, _ = parse(ir)
    origins = handle_sources(ir, definitions)
    extracts = {ex['ssa']: ex['offset'] for read in reads if read['kind'] in ('raw', 'cb')
                for ex in read['extracts']}
    fields = {layout.parameter_bytes + 4*i: name for i, name in enumerate(layout.texture_names)}
    bindings = [row.split() for row in reflected['resource_binding_rows']]
    native = [row for row in bindings if row[0] == 'bindlessTexture2D']
    native_identity = int(native[0][4][1:]) if len(native) == 1 and re.fullmatch(r'T\d+', native[0][4]) else None
    nodes = dict(re.findall(r'^!(\d+) = (.*)$', ir, re.M))
    declarations = []
    root = re.search(r'^!dx.resources = !\{!(\d+)\}$', ir, re.M)
    table = re.match(r'!\{!(\d+),', nodes[root[1]]) if root else None
    if table:
        for key in re.findall(r'!(\d+)', nodes[table[1]]):
            # DXC 可剥离可执行模块中的资源名；STAT 的名称通过资源 range id 对应该声明。
            match = re.fullmatch(r'!\{i32 (\d+), .*?, !"(?:bindlessTexture2D)?", i32 (\d+), '
                                 r'i32 (\d+), i32 (-?\d+), i32 (\d+), .*\}', nodes[key])
            if match and int(match[1]) == native_identity:
                declarations.append(tuple(map(int, match.groups())))
    if native and (len(native) != 1 or len(declarations) != 1 or native[0][1:4] != ['texture', 'f32', '2d']
                   or not native[0][-1].endswith('unbounded')
                   or declarations[0][3] not in (-1, 0xffffffff) or declarations[0][4] != 2):
        raise EditError('原生 bindlessTexture2D 不是唯一的无界二维纹理声明。')

    def dependencies(value, seen=None):
        seen = set() if seen is None else seen
        if value in seen:
            return set()
        seen.add(value)
        if value in extracts:
            return {value}
        return set().union(*(dependencies(parent, seen) for parent in re.findall(r'%\w+',
            definitions.get(value, {}).get('text', '')) if parent in definitions))

    proofs = []
    for value, origin in origins.items():
        # annotateHandle 继承来源，不重复把同一构造计为第二个消费者。
        if origin.get('annotation'):
            continue
        binding = origin.get('binding', {})
        text = origin['construction']['text']
        if binding.get('name') == 'bindlessTexture2D':
            if len(declarations) != 1:
                raise EditError('原生纹理句柄缺少可核对的数组声明。')
            identity, space, start, _, _ = declarations[0]
            if (binding['resource_class'], binding['range_id'], binding['binding'], binding['space']) != (0, identity, start, space):
                raise EditError('原生纹理声明与句柄绑定不一致。')
            match = re.fullmatch(r'call %dx.types.Handle @dx.op.createHandle\(i32 57, i8 0, '
                                 rf'i32 {identity}, i32 (%\w+|\d+), i1 (?:true|false)\)', text)
            if not match:
                match = re.fullmatch(r'call %dx.types.Handle @dx.op.createHandleFromBinding\(i32 217, '
                    rf'%dx.types.ResBind \{{ i32 {start}, i32 -?\d+, i32 {space}, i8 0 \}}, '
                    r'i32 (%\w+|\d+), i1 (?:true|false)\)', text)
                if match and f'i32 {start}, i32 -1, i32 {space}, i8 0' not in text and f'i32 {start}, i32 4294967295, i32 {space}, i8 0' not in text:
                    match = None
            if not match:
                raise EditError('原生数组句柄构造尚未验证。')
            index = match[1]
        elif origin['kind'] == 'heap-descriptor':
            # 资源堆还包含 CBV/采样器等；仅检查明确注解为非 UAV Texture2D 的句柄。
            annotations = [o['annotation']['text'] for o in origins.values()
                           if o.get('annotation') and o['construction']['value'] == value]
            if not any(re.search(r'%dx.types.ResourceProperties \{ i32 2, i32 \d+ \}', a) for a in annotations):
                continue
            if not re.fullmatch(r'call %dx.types.Handle @dx.op.createHandleFromHeap\(i32 218, '
                                r'i32 (%\w+|\d+), i1 false, i1 (?:true|false)\)', text):
                raise EditError('原生二维纹理不是已验证的资源堆句柄。')
            index = origin['descriptor']
        else:
            continue
        if not dependencies(index):
            continue
        source = index
        seen = set()
        while source not in extracts and source not in seen:
            seen.add(source)
            passthrough = re.fullmatch(r'add(?: nuw)?(?: nsw)? i32 (%\w+), 0',
                                      definitions.get(source, {}).get('text', ''))
            if not passthrough:
                raise EditError('材质动态纹理索引含未知运算，不能证明 MDF 槽身份。')
            source = passthrough[1]
        if extracts.get(source) not in fields:
            raise EditError('动态纹理索引未指向 MDF 纹理尾部槽。')
        proofs.append({'handle': value, 'index': index, 'texture': fields[extracts[source]]})
    if proofs and any(row[0] in layout.texture_names and row[1] == 'texture' for row in bindings):
        raise EditError('同一 Shader 混用固定与动态材质纹理，尚不能证明统一扩展策略。')
    return proofs
