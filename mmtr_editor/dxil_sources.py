"""单线程解析DXIL定义/使用及资源来源；只读文本，不持有编译器或GPU资源。"""
import collections
import re

SSA = r"%[\w.]+"


# 收集函数内SSA定义及消费者，供材质地址迁移拒绝未知使用形式。
def parse(ir):
    defs, uses = {}, collections.defaultdict(list)
    statements = []
    for line, text in enumerate(ir.splitlines(), 1):
        if not text.startswith('  '):
            continue
        clean = text.strip().split('  ;', 1)[0]
        match = re.fullmatch(r'(%[\w.]+) = (.*)', clean)
        value, rhs = (match[1], match[2]) if match else (None, clean)
        row = dict(value=value, line=line, text=rhs)
        statements.append(row)
        if value:
            defs[value] = row
        for operand in set(re.findall(SSA, rhs)):
            uses[operand].append(row)
    return defs, uses, statements



# 从反射绑定和句柄构造追溯资源身份，注解继承同一来源。
def handle_sources(ir, defs):
    """Retain named/static and heap origins for loads outside material candidates."""
    bindings, origins = [], {}
    for line in ir.splitlines():
        row = re.fullmatch(r'; (\S+)\s+(cbuffer|texture|UAV|sampler)\s+\S+\s+\S+\s+(CB|T|U|S)(\d+)\s+(cb|t|u|s)(\d+)(?:,space(\d+))?\s*(\d+|unbounded)\s*', line)
        if row:
            bindings.append(dict(name=row[1], resource_class={'texture': 0, 'UAV': 1, 'cbuffer': 2, 'sampler': 3}[row[2]],
                                 range_id=int(row[4]), binding=int(row[6]), space=int(row[7] or 0)))
    for value, row in defs.items():
        rhs = row['text']
        legacy = re.fullmatch(r'call %dx.types.Handle @dx.op.createHandle\(i32 57, i8 (\d+), i32 (\d+), i32 ([%\w.]+), i1 (?:false|true)\)', rhs)
        modern = re.fullmatch(r'call %dx.types.Handle @dx.op.createHandleFromBinding\(i32 217, %dx.types.ResBind \{ i32 (\d+), i32 (\d+), i32 (\d+), i8 (\d+) \}, i32 ([%\w.]+), i1 (?:false|true)\)', rhs)
        zero = 'createHandleFromBinding(i32 217, %dx.types.ResBind zeroinitializer,' in rhs
        heap = re.fullmatch(r'call %dx.types.Handle @dx.op.createHandleFromHeap\(i32 218, i32 ([%\w.]+), i1 (?:false|true), i1 (?:false|true)\)', rhs)
        if legacy:
            candidates = [x for x in bindings if (x['resource_class'], x['range_id']) == (int(legacy[1]), int(legacy[2]))]
        elif modern:
            candidates = [x for x in bindings if (x['binding'], x['space'], x['resource_class']) == (int(modern[1]), int(modern[3]), int(modern[4]))]
        elif zero:
            candidates = [x for x in bindings if x['binding'] == x['space'] == x['resource_class'] == 0]
        else:
            candidates = []
        if len(candidates) == 1:
            origins[value] = dict(kind='named-resource', binding=candidates[0], construction=row)
        elif heap:
            origins[value] = dict(kind='heap-descriptor', descriptor=heap[1], construction=row,
                                   descriptor_definition=defs.get(heap[1]))
    changed = True
    while changed:
        changed = False
        for value, row in defs.items():
            parent = re.fullmatch(r'call %dx.types.Handle @dx.op.annotateHandle\(i32 216, %dx.types.Handle (%\w+), .*', row['text'])
            if value not in origins and parent and parent[1] in origins:
                origins[value] = dict(origins[parent[1]], annotation=row)
                changed = True
    return origins
