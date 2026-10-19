"""Restricted read-only natural-loop transition model for compute shaders."""
import re
from .reconstruct import ReconstructionError


def prepare_compute_loop(graph,state_keys=None):
    """Replace header state with arbitrary typed values and cut exactly one edge.

    A candidate must match initial values, every arbitrary-state transition,
    exit/continue guards and per-iteration read events under a typed bijection.
    State ordering is a search hypothesis, never accepted as an identity proof.
    """
    loop=graph.loop
    states=[]
    for line in graph.blocks[loop['header']][:-1]:
        m=re.fullmatch(r'(%[\w.]+) = phi (float|i32) (.+)',line)
        if ' = phi ' in line and not m:raise ReconstructionError('计算循环状态类型不受支持。')
        if m:
            pairs=re.findall(r'\[ ([^,]+), %([\w.]+) \]',m[3])
            edges={p:v for v,p in pairs}
            from .material_hlsl import split_operands
            if len(pairs)!=2 or len(split_operands(m[3]))!=2 or set(edges)!={loop['preheader'],loop['latch']}:
                raise ReconstructionError('计算循环状态入口和回边不完整。')
            states.append(dict(name=m[1],type=m[2],initial=edges[loop['preheader']],feedback=edges[loop['latch']]))
    if not 1<=len(states)<=6 or len({s['feedback'] for s in states})!=len(states):
        raise ReconstructionError('计算循环状态数或反馈身份不受支持。')
    # Writes, synchronization and collectives need a stronger induction model.
    # Reject writes before the loop too: its SRV/UAV reads may alias them.
    allowed={'createHandle','createHandleFromBinding','annotateHandle','cbufferLoadLegacy','rawBufferLoad',
             'legacyF16ToF32','unary','binary','tertiary','dot2','dot3','dot4','threadId','groupId','threadIdInGroup','flattenedThreadIdInGroup'}
    for block,lines in graph.blocks.items():
        for position,line in enumerate(lines[:-1]):
            operation=re.search(r'@dx.op.([\w.]+)\(',line)
            if operation and operation[1].split('.')[0] not in allowed and block in loop['blocks']:
                raise ReconstructionError('计算循环内写入、collective、同步或未知调用尚未建模。')
            if operation and operation[1].split('.')[0] in ('rawBufferStore','atomicBinOp'):
                # Reachability on the original CFG; a writer may run only after exit.
                pending=[block];seen=set()
                while pending:
                    current=pending.pop()
                    if current in seen:continue
                    seen.add(current)
                    pending.extend(re.findall(r'label %([\w.]+)',graph.blocks[current][-1]))
                if block in loop['blocks'] or loop['header'] in seen:
                    raise ReconstructionError('计算循环内或此前写入需要跨迭代内存模型。')
    keys=tuple(range(len(states))) if state_keys is None else tuple(state_keys)
    if sorted(keys)!=list(range(len(states))):raise ReconstructionError('计算循环状态映射不是双射。')
    for s,key in zip(states,keys):
        s['key']=('compute_state',key)
        graph.loop_parameters[s['name']]=(s['type'],s['key'])
    graph.loop_states=states
    sink='__loop_backedge'
    if sink in graph.blocks:raise ReconstructionError('计算循环保留块名称冲突。')
    graph.blocks[loop['latch']][-1]=re.sub(r'label %'+re.escape(loop['header'])+r'(?=[,\s]|$)','label %'+sink,graph.blocks[loop['latch']][-1])
    graph.blocks[sink]=['ret void'];loop['backedge_sink']=sink
