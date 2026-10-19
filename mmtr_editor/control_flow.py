"""Conservative single natural-loop classification, independent of SSA values."""
import re
from .reconstruct import ReconstructionError


class NestedLoopRequired(ReconstructionError):
    """The single-loop classifier found an additional cycle/backedge."""


def natural_loop_forest(blocks):
    """Classify up to four nested/disjoint reducible loops for source emission.

    Every loop has one external incoming edge and one conditional latch/exit.
    Cutting all claimed backedges must leave a reachable DAG. This describes
    control flow only; it does not establish equivalence or termination.
    """
    successors={b:re.findall(r'label %([\w.]+)',lines[-1]) for b,lines in blocks.items()}
    predecessors={b:set() for b in blocks}
    for b,children in successors.items():
        for child in children:
            if child not in blocks:raise ReconstructionError('未知控制流目标。')
            predecessors[child].add(b)
    reachable=set();pending=['0']
    while pending:
        b=pending.pop()
        if b in reachable:continue
        reachable.add(b);pending.extend(successors[b])
    if reachable!=set(blocks):raise ReconstructionError('存在不可达块。')
    dom={b:({'0'} if b=='0' else set(blocks)) for b in blocks};changed=True
    while changed:
        changed=False
        for b in blocks:
            if b=='0':continue
            value={b}|set.intersection(*(dom[p] for p in predecessors[b]))
            if value!=dom[b]:dom[b]=value;changed=True
    backedges=[(b,c) for b,children in successors.items() for c in children if c in dom[b]]
    if len(backedges)>4 or len({h for _,h in backedges})!=len(backedges):
        raise ReconstructionError('循环数量或多latch形态超出源码重建范围。')
    loops=[]
    for latch,header in backedges:
        members={header,latch};pending=[] if header==latch else [latch]
        while pending:
            for p in predecessors[pending.pop()]:
                if p not in members:members.add(p);pending.append(p)
        entries=[(p,b) for b in members for p in predecessors[b] if p not in members]
        exits=[(b,c) for b in members for c in successors[b] if c not in members]
        if len(entries)!=1 or entries[0][1]!=header or len(exits)!=1 or exits[0][0]!=latch:
            raise ReconstructionError('自然循环需要唯一外部入口和latch出口。')
        exit_block=exits[0][1]
        if set(successors[latch])!={header,exit_block} or len(successors[latch])!=2:
            raise ReconstructionError('自然循环需要条件latch与唯一出口。')
        if not all(header in dom[b] for b in members):raise ReconstructionError('循环头未支配整个循环。')
        loops.append(dict(header=header,latch=latch,preheader=entries[0][0],exit=exit_block,blocks=members))
    for a in loops:
        for b in loops:
            if a is not b and a['blocks'] & b['blocks'] and not (a['blocks']<b['blocks'] or b['blocks']<a['blocks']):
                raise ReconstructionError('循环相交且不构成嵌套森林。')
        parents=[b for b in loops if a['blocks']<b['blocks']]
        a['parent']=min(parents,key=lambda b:len(b['blocks']))['header'] if parents else None
    visiting=set();visited=set();backedges=set(backedges)
    def visit(b):
        if b in visiting:raise ReconstructionError('切除自然回边后仍含非归约循环。')
        if b in visited:return
        visiting.add(b)
        for c in successors[b]:
            if (b,c) not in backedges:visit(c)
        visiting.remove(b);visited.add(b)
    visit('0')
    return sorted(loops,key=lambda loop:(len(dom[loop['header']]),loop['header']))


def single_natural_loop(blocks):
    successors = {b: re.findall(r'label %([\w.]+)', lines[-1]) for b, lines in blocks.items()}
    predecessors = {b: set() for b in blocks}
    for source, targets in successors.items():
        for target in targets:
            if target not in blocks:
                raise ReconstructionError('未知控制流目标。')
            predecessors[target].add(source)
    ids, low, stack, active, components = {}, {}, [], set(), []

    def visit(block):
        ids[block] = low[block] = len(ids)
        stack.append(block)
        active.add(block)
        for child in successors[block]:
            if child not in ids:
                visit(child)
                low[block] = min(low[block], low[child])
            elif child in active:
                low[block] = min(low[block], ids[child])
        if low[block] == ids[block]:
            component = set()
            while True:
                member = stack.pop()
                active.remove(member)
                component.add(member)
                if member == block:
                    break
            if len(component) > 1 or block in successors[block]:
                components.append(component)

    visit('0')
    if len(ids) != len(blocks):
        raise ReconstructionError('存在不可达块。')
    if not components:
        return None
    if len(components) != 1:
        raise ReconstructionError('仅支持单个自然循环。')
    component = components[0]
    entries = [(p, b) for b in component for p in predecessors[b] if p not in component]
    exits = [(b, c) for b in component for c in successors[b] if c not in component]
    if len(entries) != 1 or len(exits) != 1:
        raise ReconstructionError('循环需要唯一入口边和出口边。')
    preheader, header = entries[0]
    latch, exit_block = exits[0]
    if successors[latch].count(header) != 1 or set(successors[latch]) != {header, exit_block}:
        raise ReconstructionError('循环出口必须与唯一回边共享条件跳转。')
    dom = {b: ({'0'} if b == '0' else set(blocks)) for b in blocks}
    changed = True
    while changed:
        changed = False
        for block in blocks:
            if block != '0':
                updated = {block} | set.intersection(*(dom[p] for p in predecessors[block]))
                if updated != dom[block]:
                    dom[block] = updated
                    changed = True
    if not all(header in dom[b] for b in component):
        raise ReconstructionError('循环头未支配整个循环。')
    # Cutting the claimed backedge must leave a DAG. This also rejects nested
    # cycles sharing the same SCC and extra latches.
    visiting, seen = set(), set()

    def acyclic(block):
        if block in visiting:
            raise NestedLoopRequired('循环含额外回边或嵌套循环。')
        if block in seen:
            return
        visiting.add(block)
        for child in successors[block]:
            if (block, child) != (latch, header):
                acyclic(child)
        visiting.remove(block)
        seen.add(block)

    acyclic('0')
    return {'header': header, 'latch': latch, 'preheader': preheader,
            'exit': exit_block, 'blocks': component}
