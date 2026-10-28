"""单线程解析DXIL定义/使用及资源来源；只读文本，不持有编译器或GPU资源。"""
import collections
from dataclasses import dataclass
import re

SSA = r"%[\w.]+"
_SSA_RE = re.compile(SSA)
_DEFINITION_RE = re.compile(r'(%[\w.]+) = (.*)')


@dataclass(frozen=True)
class ParsedIr:
    """同一 IR 文本的只读分析图；调用方只读，不得原地修改节点或边。"""
    text: str
    definitions: dict
    uses: dict
    statements: list


# 收集函数内SSA定义及消费者，供材质地址迁移拒绝未知使用形式。
def parse(ir):
    defs, uses = {}, collections.defaultdict(list)
    statements = []
    for line, text in enumerate(ir.splitlines(), 1):
        if not text.startswith('  '):
            continue
        clean = text.strip().split('  ;', 1)[0]
        match = _DEFINITION_RE.fullmatch(clean)
        value, rhs = (match[1], match[2]) if match else (None, clean)
        row = dict(value=value, line=line, text=rhs)
        statements.append(row)
        if value:
            defs[value] = row
        for operand in set(_SSA_RE.findall(rhs)):
            uses[operand].append(row)
    return defs, uses, statements


# 为同一次扩展调用建立精确文本对应的分析图，避免多个只读消费者重复分词。
def parse_graph(ir):
    definitions, uses, statements = parse(ir)
    return ParsedIr(ir, definitions, uses, statements)


# 只在文本对象确实是同一份 IR 时复用图；错误图不能被静默用于不同文本。
def ensure_graph(ir, graph=None):
    if graph is None:
        return parse_graph(ir)
    if not isinstance(graph, ParsedIr) or graph.text != ir:
        raise ValueError('IR 分析图与当前文本不一致。')
    return graph


# 从反射绑定和句柄构造追溯资源身份，注解继承同一来源。
def handle_sources(ir, defs):
    """Retain named/static and heap origins for loads outside material candidates."""
    bindings, origins = [], {}
    for line in ir.splitlines():
        if not line.startswith('; '):
            continue
        row = re.fullmatch(r'; (\S+)\s+(cbuffer|texture|UAV|sampler)\s+\S+\s+\S+\s+(CB|T|U|S)(\d+)\s+(cb|t|u|s)(\d+)(?:,space(\d+))?\s*(\d+|unbounded)\s*', line)
        if row:
            bindings.append(dict(name=row[1], resource_class={'texture': 0, 'UAV': 1, 'cbuffer': 2, 'sampler': 3}[row[2]],
                                 range_id=int(row[4]), binding=int(row[6]), space=int(row[7] or 0)))
    for value, row in defs.items():
        rhs = row['text']
        # 只有Handle构造能产生资源来源；跳过大量算术指令，避免逐项运行资源正则。
        if not rhs.startswith('call %dx.types.Handle @dx.op.'):
            continue
        legacy = re.fullmatch(r'call %dx.types.Handle @dx.op.createHandle\(i32 57, i8 (\d+), i32 (\d+), i32 ([%\w.]+), i1 (?:false|true)\)', rhs)
        modern = re.fullmatch(r'call %dx.types.Handle @dx.op.createHandleFromBinding\(i32 217, %dx.types.ResBind \{ i32 (\d+), i32 (-?\d+), i32 (\d+), i8 (\d+) \}, i32 ([%\w.]+), i1 (?:false|true)\)', rhs)
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
    annotations = [(value, row, re.fullmatch(
        r'call %dx.types.Handle @dx.op.annotateHandle\(i32 216, %dx.types.Handle (%\w+), .*', row['text']))
        for value, row in defs.items() if row['text'].startswith('call %dx.types.Handle @dx.op.annotateHandle(')]
    changed = True
    while changed:
        changed = False
        for value, row, parent in annotations:
            if value not in origins and parent and parent[1] in origins:
                origins[value] = dict(origins[parent[1]], annotation=row)
                changed = True
    return origins
