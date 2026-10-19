"""Recover dense 32-bit nested StructuredBuffer layouts from two evidence sources."""
import re
from .reconstruct import ReconstructionError
from .material_hlsl import split_operands, Value


def nested_struct_layout(ir, typename, llvm_name, members, stride):
    definitions=dict(re.findall(r'^(%[\w.]+) = type \{ (.+) \}$',ir,re.M))
    declarations,slots={},{}

    def visit(name,llvm,fields,base_offset,prefix,ancestors):
        if not re.fullmatch(r'\w+',name) or llvm in ancestors or len(ancestors)>8:
            raise ReconstructionError('嵌套结构类型递归、名称或深度不受支持。')
        actual=split_operands(definitions.get(llvm,''))
        if len(actual)!=len(fields):raise ReconstructionError('嵌套结构字段数量与LLVM不符。')
        cursor=base_offset
        lines=[]
        for var,actual_type in zip(fields,actual):
            n=re.fullmatch(r'(\w+)(?:\[(\d+)\])?',var['name'])
            if not n or var['offset']!=cursor:
                raise ReconstructionError('嵌套结构存在填充、重叠或非连续偏移。')
            field_prefix=prefix+n[1]
            if 'members' in var:
                if n[2] or actual_type not in definitions or not re.fullmatch(r'%(?:hostlayout\.)?struct\.'+re.escape(var['struct_type']),actual_type):
                    raise ReconstructionError('嵌套结构LLVM类型或结构数组不受支持。')
                cursor=visit(var['struct_type'],actual_type,var['members'],cursor,field_prefix+'.',ancestors|{llvm})
                lines.append(f'    {var["struct_type"]} {n[1]};')
                continue
            t=re.fullmatch(r'(?:(row_major|column_major) )?(float|uint|int)([1-4])?(?:x([1-4]))?',var['type'])
            if not t:raise ReconstructionError('未知嵌套结构标量字段。')
            major,base,width,columns=t[1],t[2],int(t[3] or 1),int(t[4] or 0)
            count=int(n[2] or 1)
            if not 1<=count<=4096:raise ReconstructionError('嵌套结构数组范围不受支持。')
            scalar='float' if base=='float' else 'i32'
            expected=scalar if width==1 else f'<{width} x {scalar}>'
            if columns:
                rows=width if major=='row_major' else columns
                vector=columns if major=='row_major' else width
                expected=f'[{rows} x <{vector} x {scalar}>]'
                if actual_type.startswith('%class.matrix.'):
                    actual_type=definitions.get(actual_type,actual_type)
            if n[2]:expected=f'[{count} x {expected}]'
            if actual_type!=expected:raise ReconstructionError('嵌套结构字段类型与LLVM不符。')
            lines.append(f'    {var["type"]} {var["name"]};')
            for a in range(count):
                suffix=f'[{a}]' if n[2] else ''
                for row in range(width):
                    for col in range(columns or 1):
                        local=(row*columns+col if major=='row_major' else col*width+row) if columns else row
                        slot=cursor//4+a*width*(columns or 1)+local
                        if cursor%4 or slot in slots:raise ReconstructionError('嵌套字段不对齐或重叠。')
                        access=field_prefix+suffix+(f'[{row}][{col}]' if columns else ('.'+'xyzw'[row] if width>1 else ''))
                        slots[slot]=Value(base,access)
            cursor+=4*count*width*(columns or 1)
        declaration=f'struct {name} {{\n'+'\n'.join(lines)+'\n};'
        if name in declarations and declarations[name]!=declaration:
            raise ReconstructionError('同名嵌套结构布局不一致。')
        declarations[name]=declaration
        return cursor

    size=visit(typename,llvm_name,members,0,'',set())
    if size!=stride or stride%4 or set(slots)!=set(range(stride//4)):
        raise ReconstructionError('嵌套结构长度与反射步长不符。')
    return list(declarations.values()),slots
