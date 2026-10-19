"""Exact bounded triangle mesh entry metadata and indexed output call shapes."""
import re
from .reconstruct import ReconstructionError,function_body


def mesh_interface(ir):
    nodes=dict(re.findall(r'^!(\d+) = !\{(.*)\}$',ir,re.M))
    model=re.search(r'^!dx.shaderModel = !\{!(\d+)\}$',ir,re.M)
    if not model or nodes.get(model[1])!='!"ms", i32 6, i32 6':
        raise ReconstructionError('网格重建当前仅支持ms_6_6。')
    entry,body=function_body(ir)
    resource_root=re.search(r'^!dx.resources = !\{!(\d+)\}$',ir,re.M)
    resource_sides=nodes.get(resource_root[1],'').split(', ') if resource_root else ['null']*4
    if len(resource_sides)!=4 or resource_sides[1]!='null':
        raise ReconstructionError('网格验证当前仅支持只读资源。')
    root=re.search(r'^!dx.entryPoints = !\{!(\d+)\}$',ir,re.M)
    declaration=nodes.get(root[1],'') if root else ''
    match=re.fullmatch(r'void \(\)\* @'+re.escape(entry)+r', !"'+re.escape(entry)+r'", !(\d+), (?:null|!\d+), !(\d+)',declaration)
    props=nodes.get(match[2],'') if match else ''
    properties=re.fullmatch(r'(?:i32 0, i64 (\d+), )?i32 9, !(\d+)(?:, i32 5, !(\d+))?',props)
    allowed=16|256|8388608|(1<<30)
    if not properties or int(properties[1] or 0)&~allowed or properties[3] and nodes.get(properties[3])!='i32 0':
        raise ReconstructionError('未知网格入口flags、属性或wave形态。')
    mesh=re.fullmatch(r'!(\d+), i32 (\d+), i32 (\d+), i32 2, i32 0',nodes.get(properties[2],''))
    dimensions=re.fullmatch(r'i32 (\d+), i32 (\d+), i32 (\d+)',nodes.get(mesh[1],'')) if mesh else None
    if not dimensions:raise ReconstructionError('仅支持无payload的triangle网格入口。')
    threads=tuple(map(int,dimensions.groups()));vertices,primitives=int(mesh[2]),int(mesh[3])
    if not (all(1<=n<=128 for n in threads) and threads[0]*threads[1]*threads[2]<=128 and 1<=vertices<=256 and 1<=primitives<=256):
        raise ReconstructionError('网格线程组或最大输出数超出受限范围。')
    sides=nodes.get(match[1],'').split(', ')
    if len(sides)!=3 or sides[0]!='null' or any(not re.fullmatch(r'!\d+',s) for s in sides[1:]):
        raise ReconstructionError('网格入口需要无输入及独立顶点/图元输出签名。')
    def signature(side,primitive):
        result={};semantics=set()
        listing=nodes.get(side[1:],'');refs=re.findall(r'!(\d+)',listing)
        if not refs or ', '.join('!'+r for r in refs)!=listing:raise ReconstructionError('网格签名引用不完整。')
        for ref in refs:
            m=re.fullmatch(r'i32 (\d+), !"(\w+)", i8 (5|9), i8 (\d+), !(\d+), i8 (\d+), i32 1, i8 ([1-4]), i32 (\d+), i8 ([0-3]), (null|!\d+)',nodes.get(ref,''))
            if not m:raise ReconstructionError('网格签名类型、行数或形态未知。')
            index=re.fullmatch(r'i32 (\d+)',nodes.get(m[5],''))
            position=m[2].lower()=='sv_position'
            expected_kind=3 if position and not primitive else 0
            if int(m[4])!=expected_kind or m[2].lower().startswith('sv_') and not position or primitive and position:
                raise ReconstructionError('未知网格系统语义或顶点/图元分类。')
            interp={1:'nointerpolation',2:'linear',4:'noperspective'}.get(int(m[6]))
            if not index or interp is None or primitive and interp!='nointerpolation' or m[3]=='5' and interp!='nointerpolation':
                raise ReconstructionError('网格签名索引或插值模式不受支持。')
            if position and (m[3]!='9' or m[7]!='4' or index[1]!='0'):
                raise ReconstructionError('网格SV_Position必须是float4。')
            key=int(m[1]);semantic=(m[2].lower(),int(index[1]))
            if key in result or semantic in semantics or int(m[9])+int(m[7])>4:
                raise ReconstructionError('网格签名重复或packing列越界。')
            if m[10]!='null' and not re.fullmatch(r'i32 3, i32 (\d+)',nodes.get(m[10][1:],'')):
                raise ReconstructionError('未知网格签名属性。')
            result[key]=dict(name=m[2],index=int(index[1]),width=int(m[7]),type={'5':'uint','9':'float'}[m[3]],interp=interp,
                             packing_row=int(m[8]),packing_column=int(m[9]),usage=nodes.get(m[10][1:]) if m[10]!='null' else None)
            semantics.add(semantic)
        return result
    forbidden=r'@dx.op.(?:discard|sample|sampleGrad|sampleIndex|quadOp|loadInput|storeOutput|rawBufferStore|atomicBinOp|barrier|dispatchMesh|wave\w*)(?:\.|\()'
    if re.search(forbidden,body) or re.search(r'@dx.op.unary.f32\(i32 (?:83|84|85|86),',body):
        raise ReconstructionError('网格入口暂不支持此像素、同步或可写内存操作。')
    interface=dict(entry=entry,body=body,profile='ms_6_6',threads=threads,max_vertices=vertices,max_primitives=primitives,
                   topology='triangle',vertices=signature(sides[1],False),primitives=signature(sides[2],True))
    validate_mesh_output_flow(interface)
    return interface


def validate_mesh_output_flow(interface):
    """One count call, acyclic CFG, counts dominate every indexed output.

    Dynamic counts/indices are preserved, not range- or uniformity-proved.
    Interface bounds and constant operands are checked separately.
    """
    blocks={'0':[]};current='0';counts=[];outputs=[]
    for raw in interface['body'].splitlines():
        label=re.match(r'(?:; <label>:(\d+)|([\w.]+):)',raw.strip())
        if label:
            current=label[1] or label[2]
            if current in blocks:raise ReconstructionError('重复网格基本块。')
            blocks[current]=[]
            continue
        line=raw.split(';',1)[0].strip()
        if not line:continue
        position=len(blocks[current]);blocks[current].append(line)
        if re.search(r'@dx.op.(?:setMeshOutputCounts|emitIndices|storeVertexOutput|storePrimitiveOutput)(?:\.|\()',line):
            output=mesh_output_call(line,interface)
            (counts if output['name']=='setMeshOutputCounts' else outputs).append((current,position))
    if len(counts)!=1:raise ReconstructionError('网格入口需要恰好一次静态SetMeshOutputCounts调用。')
    visiting=set();done={}
    def visit(block,initialized):
        if block in visiting:raise ReconstructionError('网格输出控制流暂不支持循环。')
        if block not in blocks or not blocks[block]:raise ReconstructionError('未知或空网格基本块。')
        state=(block,initialized)
        if state in done:return
        visiting.add(block)
        for pos,line in enumerate(blocks[block]):
            if (block,pos)==counts[0]:initialized=True
            elif (block,pos) in outputs and not initialized:
                raise ReconstructionError('SetMeshOutputCounts必须支配每个网格输出。')
        term=blocks[block][-1]
        if term!='ret void' and not term.startswith('br '):raise ReconstructionError('未知网格控制流终结。')
        for child in set(re.findall(r'label %([\w.]+)',term)):visit(child,initialized)
        visiting.remove(block);done[state]=True
    visit('0',False)
    if {b for b,_ in done}!=set(blocks):raise ReconstructionError('不可达网格基本块。')


def mesh_output_call(body,interface):
    """Validate types/opcode/row/lane before source emission or comparison."""
    from .material_hlsl import split_operands,strip_type
    call=re.fullmatch(r'call void @dx.op.(setMeshOutputCounts|emitIndices|storeVertexOutput\.(?:f32|i32)|storePrimitiveOutput\.(?:f32|i32))\((.*)\)',body)
    if not call:raise ReconstructionError('未知网格输出调用。')
    name=call[1];args=split_operands(call[2]);values=[strip_type(a) for a in args]
    if name in ('setMeshOutputCounts','emitIndices'):
        opcode,count=(168,3) if name=='setMeshOutputCounts' else (169,5)
        if len(args)!=count or args[0]!=f'i32 {opcode}' or any(not a.startswith('i32 ') for a in args):
            raise ReconstructionError('网格计数或索引类型、操作码不符。')
        if name=='setMeshOutputCounts':
            for token,limit in zip(values[1:],(interface['max_vertices'],interface['max_primitives'])):
                if token.lstrip('-').isdigit() and not 0<=int(token)<=limit:
                    raise ReconstructionError('常量网格输出计数超出声明上限。')
        else:
            for token,limit in zip(values[1:],(interface['max_primitives'],)+(interface['max_vertices'],)*3):
                if token.lstrip('-').isdigit() and not 0<=int(token)<limit:
                    raise ReconstructionError('常量三角形或顶点索引越界。')
        return dict(name=name,args=args,values=values)
    primitive=name.startswith('storePrimitive');signature=interface['primitives' if primitive else 'vertices']
    expected_type='float' if name.endswith('f32') else 'i32'
    if not (len(args)==6 and args[0]==('i32 172' if primitive else 'i32 171') and args[1].startswith('i32 ') and args[2]=='i32 0'
            and args[3].startswith('i8 ') and args[4].startswith(expected_type+' ') and args[5].startswith('i32 ')
            and values[1].isdigit() and int(values[1]) in signature and values[3].isdigit()):
        raise ReconstructionError('网格输出签名、类型、操作码或行列不符。')
    field=signature[int(values[1])];lane=int(values[3]);limit=interface['max_primitives' if primitive else 'max_vertices']
    if lane>=field['width'] or field['type']!=('float' if expected_type=='float' else 'uint'):
        raise ReconstructionError('网格输出通道或标量类型不符。')
    if values[5].lstrip('-').isdigit() and not 0<=int(values[5])<limit:
        raise ReconstructionError('常量网格输出数组索引越界。')
    return dict(name=name,args=args,values=values,field=field,lane=lane,primitive=primitive)
