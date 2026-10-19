"""Restricted two-level CS transition and ordered-event model.

Cut each backedge and replace each carried state with an independent typed
parameter. Compare both loops' initial values, arbitrary-iteration feedback,
entry/continue/exit guards and complete boundary-marked event traces. Reads
remain occurrence-bearing external observations; no shared-store forwarding,
fixed unrolling, termination, scheduling or GPU memory-model claim is made.
The production comparator routes two-level CS loops here; other shapes reject.
"""
import re
from itertools import permutations
from .control_flow import natural_loop_forest
from .material_hlsl import split_operands
from .reconstruct import ReconstructionError


def prepare_nested_compute(graph, state_keys=None):
    loops=natural_loop_forest(graph.blocks)
    if len(loops)!=2 or loops[0]['parent'] is not None or loops[1]['parent']!=loops[0]['header']:
        raise ReconstructionError('研究模型需要恰好两层嵌套自然循环。')
    if graph.local_arrays:
        raise ReconstructionError('嵌套循环与私有数组组合尚未建模。')
    if state_keys is not None and set(state_keys)!={0,1}:
        raise ReconstructionError('嵌套循环状态映射需要两层完整映射。')
    for key,loop in enumerate(loops):
        loop['key']=key
        states=[]
        for line in graph.blocks[loop['header']][:-1]:
            m=re.fullmatch(r'(%[\w.]+) = phi (float|i32) (.+)',line)
            if ' = phi ' in line and not m:
                raise ReconstructionError('嵌套循环状态类型不受支持。')
            if not m:continue
            pairs=re.findall(r'\[ ([^,]+), %([\w.]+) \]',m[3]);edges={p:v for v,p in pairs}
            if len(pairs)!=2 or len(split_operands(m[3]))!=2 or set(edges)!={loop['preheader'],loop['latch']}:
                raise ReconstructionError('嵌套循环状态入口与反馈边不完整。')
            states.append(dict(name=m[1],type=m[2],initial=edges[loop['preheader']],feedback=edges[loop['latch']]))
        if not 1<=len(states)<=6 or len({s['feedback'] for s in states})!=len(states):
            raise ReconstructionError('嵌套循环状态数或反馈身份超出受限模型。')
        keys=tuple(range(len(states))) if state_keys is None else tuple(state_keys[key])
        if sorted(keys)!=list(range(len(states))):
            raise ReconstructionError('嵌套循环状态映射不是双射。')
        for s,index in zip(states,keys):
            s['key']=('nested_compute_state',key,index)
            graph.loop_parameters[s['name']]=(s['type'],s['key'])
        loop['states']=states
        graph.loop_states.extend(states)
    # Initial states must come from outside the loop, rather than referring to
    # this loop's arbitrary parameter or a descendant's arbitrary parameter.
    # Check the SSA dependency closure before erasing any header definitions.
    ownership={s['name']:loop['key'] for loop in loops for s in loop['states']}
    for loop in loops:
        for state in loop['states']:
            pending=[state['initial']];seen=set()
            while pending:
                token=pending.pop()
                if token in seen:continue
                seen.add(token)
                if token in ownership:
                    if ownership[token]>=loop['key']:
                        raise ReconstructionError('嵌套循环初始值依赖本层或子层状态。')
                    continue
                if token in graph.defs:
                    pending.extend(re.findall(r'%[\w.]+',graph.defs[token][1]))
    for loop in loops:
        sink=f'__nested_backedge_{loop["key"]}'
        if sink in graph.blocks:raise ReconstructionError('嵌套循环保留块名称冲突。')
        graph.blocks[loop['latch']][-1]=re.sub(r'label %'+re.escape(loop['header'])+r'(?=[,\s]|$)','label %'+sink,graph.blocks[loop['latch']][-1])
        graph.blocks[sink]=['ret void'];loop['backedge_sink']=sink
    graph.nested_loops=loops


def nested_observables(graph):
    return tuple(dict(
        key=loop['key'],parent=None if loop['parent'] is None else 0,
        initial=sorted((s['key'],s['type'],graph.restrict_vertex_value(graph.value(s['initial'],s['type']),graph.edge_guard(loop['preheader'],loop['header']))) for s in loop['states']),
        feedback=sorted((s['key'],s['type'],graph.restrict_vertex_value(graph.value(s['feedback'],s['type']),graph.reach(loop['latch']))) for s in loop['states']),
        entry_guard=graph.edge_guard(loop['preheader'],loop['header']),
        continue_guard=graph.edge_guard(loop['latch'],loop['backedge_sink']),
        exit_guard=graph.edge_guard(loop['latch'],loop['exit']),
    ) for loop in graph.nested_loops)


def install_counter_invariants(graph):
    """Prove only unsigned unit-step counters entered below a stable bound.

    Base: the actual entry guard implies initial < bound. Step: given current
    < bound, current+1 cannot overflow and is <= bound; either ult continuation
    or ne continuation establishes current < bound for the next iteration.
    No nsw/nuw flag, float range assumption or fixed trip count is trusted.
    """
    proven=[]
    for loop in graph.nested_loops:
        term=graph.blocks[loop['latch']][-1]
        branch=re.fullmatch(r'br i1 (%[\w.]+), label %([\w.]+), label %([\w.]+)',term)
        comparison=graph.defs.get(branch[1],('', ''))[1] if branch else ''
        compare=re.fullmatch(r'icmp (ult|eq|ne) i32 (%[\w.]+), (.+)',comparison)
        if not compare:continue
        continuation_true=branch[2]==loop['backedge_sink']
        if not (compare[1] in ('ult','ne') and continuation_true or compare[1]=='eq' and not continuation_true):continue
        state=next((s for s in loop['states'] if s['type']=='i32' and s['feedback']==compare[2]),None)
        if not state:continue
        increment=graph.defs.get(state['feedback'],('', ''))[1]
        if not re.fullmatch(r'add(?: nuw| nsw)* i32 '+re.escape(state['name'])+r', 1',increment):continue
        pending=[compare[3]];seen=set();stable=True
        while pending:
            token=pending.pop()
            if token in seen:continue
            seen.add(token)
            if token in graph.loop_parameters:
                if graph.loop_parameters[token][1][1]>=loop['key']:stable=False
                continue
            if token in graph.defs:
                block,body=graph.defs[token]
                if block in loop['blocks'] and ('@dx.op.rawBufferLoad' in body or token in graph.shared_loads or re.search(r'@dx.op.(?:wave|atomic)',body)):
                    stable=False
                pending.extend(re.findall(r'%[\w.]+',body))
        if not stable:continue
        bound=graph.value(compare[3],'i32');initial=graph.value(state['initial'],'i32')
        entry=graph.edge_guard(loop['preheader'],loop['header'])
        initial_less=graph.boolean(graph.intern(('icmp','ult','i32',(initial,bound))))
        if graph.logic('and',entry,graph.logic('xor',initial_less,'1'))!='0':continue
        proven.append(dict(loop=loop['key'],state=state['key'],next=graph.value(state['feedback'],'i32'),bound=bound))
    graph.nested_counter_invariants=proven
    # All later guard/expression identities must use the proved normalization,
    # including wave contexts and ordered-prefix read identities.
    for name in ('cache','boolean_cache','reach_cache','kill_entry_cache','kill_exit_cache','nested_prefix_cache','nested_entry_cache'):
        if hasattr(graph,name):getattr(graph,name).clear()


def rewrite_unobserved_high_bits(graph,value,bits,memo=None):
    """Rewrite ashr to lshr only when their sign-fill bits are discarded.

    The caller observes only low `bits` bits (a following constant left shift
    discards the rest). Modular add/mul/bitwise low bits depend only on the same
    low bits of their operands. Right shifts require additional input bits;
    if sign fill can survive, keep the original signed shift. No float rules.
    Rewritten intermediates must only be used in the caller's truncated use.
    """
    if memo is None:memo={}
    if (value,bits) in memo:return memo[(value,bits)]
    node=graph.nodes[value];result=value
    if len(node)==4 and node[2]=='i32' and node[0] in ('add','mul','and','or','xor'):
        operands=[rewrite_unobserved_high_bits(graph,arg,bits,memo) for arg in node[3]]
        result=graph.integer_add(operands) if node[0]=='add' else graph.intern((node[0],node[1],node[2],tuple(sorted(operands))))
    elif len(node)==4 and node[2]=='i32' and node[0] in ('ashr','lshr','shl'):
        first,second=node[3];amount=graph.nodes[second]
        if amount[0]=='i32' and isinstance(amount[1],int) and 0<=amount[1]<32:
            shift=amount[1];needed=bits+shift if node[0]!='shl' else max(0,bits-shift)
            if needed<=32:
                first=rewrite_unobserved_high_bits(graph,first,needed,memo)
                op='lshr' if node[0]=='ashr' else node[0]
                result=graph.intern((op,node[1],node[2],(first,second)))
    memo[(value,bits)]=result
    return result


def event_prefix_identity(graph,block,position):
    """Use the complete guarded event history, independent of block layout.

    Multiple arriving histories are retained, never collapsed to one lexical
    ordinal. Earlier read results recursively depend on their earlier history;
    cutting the loop edges makes this dependency acyclic. Explicit bounds fail
    closed. Boundary markers distinguish nested iteration phases.
    """
    if not hasattr(graph,'nested_prefix_cache'):
        graph.nested_prefix_cache={};graph.nested_entry_cache={};graph.nested_prefix_active=set()
    location=(block,position)
    if location in graph.nested_prefix_cache:return graph.nested_prefix_cache[location]
    if location in graph.nested_prefix_active:
        raise ReconstructionError('嵌套事件前缀存在循环依赖。')
    graph.nested_prefix_active.add(location)
    def entry(target):
        if target in graph.nested_entry_cache:return graph.nested_entry_cache[target]
        histories={(): '1'} if target=='0' else {}
        for pred in graph.predecessors[target]:
            for events,guard in prefix(pred,len(graph.blocks[pred])-1).items():
                guard=graph.logic('and',guard,graph.edge_guard(pred,target))
                if guard=='0':continue
                for loop in graph.nested_loops:
                    if pred==loop['latch'] and target==loop['exit']:
                        events+=(("nested_loop_exit",loop['key']),)
                histories[events]=graph.logic('or',histories.get(events,'0'),guard)
        for loop in graph.nested_loops:
            if target==loop['header']:
                histories={events+(("nested_loop_enter",loop['key']),):guard for events,guard in histories.items()}
        if len(histories)>512:raise ReconstructionError('嵌套事件前缀超过512条历史。')
        graph.nested_entry_cache[target]=histories
        return histories
    def prefix(target,stop):
        histories=entry(target)
        for pos,line in enumerate(graph.blocks[target][:stop]):
            if (target,pos) in graph.compute_events:
                event=graph.compute_event(line,target,pos)
                histories={events+(event,):guard for events,guard in histories.items()}
                if any(len(events)>128 for events in histories):
                    raise ReconstructionError('嵌套事件前缀超过128个事件。')
        return histories
    histories=prefix(block,position)
    result=graph.intern(('nested_event_prefix',tuple(sorted((guard,events) for events,guard in histories.items()))))
    graph.nested_prefix_cache[location]=result
    graph.nested_prefix_active.remove(location)
    return result


def nested_event_paths(graph,by_block):
    """Merge equal incoming histories before propagating through the DAG.

    Empty conditional diamonds must not multiply traversal exponentially. Only
    identical complete event/boundary histories merge, by exact BDD guard OR.
    Different histories remain distinct and have explicit memory/path bounds.
    """
    incoming={block:{} for block in graph.blocks};incoming['0'][()]='1'
    remaining={block:len(parents) for block,parents in graph.predecessors.items()}
    pending=['0'];paths={};visits=0;processed=0
    while pending:
        block=pending.pop();processed+=1;term=graph.blocks[block][-1]
        children=list(dict.fromkeys(re.findall(r'label %([\w.]+)',term)))
        for events,guard in incoming[block].items():
            visits+=1
            for loop in graph.nested_loops:
                if block==loop['header']:events+=((('nested_loop_enter',loop['key']),'0'),)
            events+=by_block[block]
            if len(events)>128 or visits>16384:
                raise ReconstructionError(f'嵌套计算路径超出受限模型：events={len(events)}, visits={visits}。')
            if term=='ret void':
                sink=next((loop for loop in graph.nested_loops if block==loop['backedge_sink']),None)
                events+=((('nested_loop_continue',sink['key']) if sink else ('nested_function_return',),'0'),)
                paths[events]=graph.logic('or',paths.get(events,'0'),guard)
                if len(paths)>512:raise ReconstructionError('嵌套计算路径超过512条独立事件序列。')
            for child in children:
                edge_guard=graph.logic('and',guard,graph.edge_guard(block,child))
                if edge_guard=='0':continue
                history=events
                for loop in graph.nested_loops:
                    if block==loop['latch'] and child==loop['exit']:
                        history+=((('nested_loop_exit',loop['key']),'0'),)
                incoming[child][history]=graph.logic('or',incoming[child].get(history,'0'),edge_guard)
                if len(incoming[child])>512:raise ReconstructionError('嵌套计算到达历史超过512条。')
        for child in children:
            remaining[child]-=1
            if remaining[child]==0:pending.append(child)
        # Completed predecessors need not retain their complete histories.
        incoming[block].clear()
    if processed!=len(graph.blocks):raise ReconstructionError('嵌套计算事件CFG仍含环或不可达块。')
    return paths


def compare_nested_compute_dataflow(left_ir,right_ir,*,max_bijections=4096):
    """Restricted comparison; never silently falls back to compilation.

    Search outer then inner typed bijections. Initial expressions can depend
    only on ancestor parameters, so an exact mismatch at this stage safely
    prunes all descendant assignments. A full match still requires every
    feedback, guard and ordered memory/atomic/collective path to match.
    """
    from .verify_ir import Graph
    left=Graph(left_ir,nested_compute=True);right=Graph(right_ir,nested_compute=True)
    expected=left.observables();actual=right.observables();checked=0;last=right
    identity={loop['key']:tuple(range(len(loop['states']))) for loop in right.nested_loops}
    def search(depth,keys,graph):
        nonlocal checked,last,actual
        if depth==2:
            last=graph;actual=graph.observables()
            return graph if expected==actual else None
        source=left.nested_loops[depth]['states'];candidate=graph.nested_loops[depth]['states']
        if len(source)!=len(candidate):return None
        for mapping in permutations(range(len(candidate))):
            if any(s['type']!=source[index]['type'] for s,index in zip(candidate,mapping)):continue
            # Ancestor mappings are fixed; current header parameters cannot
            # occur in these initial expressions (checked by prepare).
            left_loop=left.nested_loops[depth];right_loop=graph.nested_loops[depth]
            left_guard=left.edge_guard(left_loop['preheader'],left_loop['header'])
            right_guard=graph.edge_guard(right_loop['preheader'],right_loop['header'])
            if any(left.restrict_vertex_value(left.value(source[index]['initial'],s['type']),left_guard)!=graph.restrict_vertex_value(graph.value(s['initial'],s['type']),right_guard) for s,index in zip(candidate,mapping)):
                continue
            checked+=1
            if checked>max_bijections:raise ReconstructionError('嵌套循环类型双射搜索超出受限模型。')
            selected={**keys,depth:mapping}
            attempt=graph if keys[depth]==mapping else Graph(right_ir,nested_compute=True,nested_state_keys=selected)
            found=search(depth+1,selected,attempt)
            if found is not None:return found
        return None
    found=search(0,identity,right)
    if found is not None:right=found;actual=right.observables()
    else:right=last
    differences=[dict(kind=k,left=expected.get(k),right=actual.get(k)) for k in sorted(expected.keys()|actual.keys()) if expected.get(k)!=actual.get(k)]
    # If initial mismatch pruned before reaching a leaf, identity observables
    # still explain the rejection; equal identity is itself a valid mapping.
    return dict(equal=not differences,model='two_level_cs_arbitrary_state_ordered_events',
                state_bijections_checked=checked,
                last_candidate_state_keys=[list(s['key']) for loop in right.nested_loops for s in loop['states']],
                state_mapping=([dict(loop=loop['key'],source=left.nested_loops[loop['key']]['states'][s['key'][2]]['name'],candidate=s['name'],type=s['type']) for loop in right.nested_loops for s in loop['states']] if not differences else None),
                loop_state_counts=[[len(l['states']) for l in g.nested_loops] for g in (left,right)],
                event_path_counts=[len(expected['ordered_memory_blocks']),len(actual['ordered_memory_blocks'])],
                differences=differences,
                counter_invariants=[[dict(loop=i['loop'],state=i['state'],bound=i['bound']) for i in g.nested_counter_invariants] for g in (left,right)],
                rules=['Both loop typed state bijections, initial values, arbitrary-state feedback and entry/continue/exit guards match',
                       'Complete guarded memory/atomic/collective histories include nested enter/exit/continue/function-return boundaries',
                       'Unsigned +1 counters use ult/ne equivalence only after proving entry below an invariant bound',
                       'Signed shift sign fill may change only when a following left shift provably discards all affected bits',
                       'Equal event prefixes merge by exact BDD guards; 128 events, 512 histories and 16384 expansions bound the model'],
                limitations=['Conditional structural trace evidence under matching external read results; no general GPU/IEEE, scheduling, race, termination or performance proof.'])
