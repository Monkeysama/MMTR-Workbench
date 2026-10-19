"""Validate bounded private uint arrays fully initialized in one acyclic block."""
import re
from .reconstruct import ReconstructionError


def analyze_local_arrays(blocks):
    defs, locations = {}, {}
    for block, lines in blocks.items():
        for position, line in enumerate(lines):
            m = re.fullmatch(r'(%[\w.]+) = (.+)', line)
            if m:
                defs[m[1]] = m[2]
                locations[m[1]] = block, position
    arrays, pointers, loads, stores, ignored = {}, {}, {}, {}, set()
    for name, body in defs.items():
        if body.startswith('alloca '):
            if body != 'alloca [4 x i32], align 4' or locations[name][0] != '0':
                raise ReconstructionError('仅支持入口中的私有uint[4]数组。')
            arrays[name] = {'values': {}, 'block': None, 'last_store': -1}
    if not arrays:
        return arrays, pointers, loads, stores, ignored
    lifetime_casts, lifetime_events = {}, {}
    for name, body in defs.items():
        cast = re.fullmatch(r'bitcast \[4 x i32\]\* (%[\w.]+) to i8\*', body)
        if cast and cast[1] in arrays:
            lifetime_casts[name] = cast[1]
            ignored.add(locations[name])
    for block, lines in blocks.items():
        for position,line in enumerate(lines):
            life = re.fullmatch(r'call void @llvm.lifetime.(start|end)\(i64 16, i8\* (%[\w.]+)\)(?: #\d+)?(?:, !noalias !\d+)?', line)
            if life and life[2] in lifetime_casts:
                lifetime_events.setdefault(lifetime_casts[life[2]], []).append((life[1],block,position))
                ignored.add((block,position))
    for name, body in defs.items():
        m = re.fullmatch(r'getelementptr(?: inbounds)? \[4 x i32\], \[4 x i32\]\* (%[\w.]+), i32 0, i32 (.+)', body)
        if m and m[1] in arrays:
            pointers[name] = (m[1], m[2])
    for block, lines in blocks.items():
        for position, line in enumerate(lines):
            store = re.fullmatch(r'store i32 (.+), i32\* (%[\w.]+), align 4(?:, !(?:tbaa|noalias|alias.scope) !\d+)*', line)
            load = re.fullmatch(r'(%[\w.]+) = load i32, i32\* (%[\w.]+), align 4(?:, !(?:tbaa|noalias|alias.scope) !\d+)*', line)
            if store and store[2] in pointers:
                array, index = pointers[store[2]]
                item = arrays[array]
                if not index.isdigit() or not 0 <= int(index) < 4 or int(index) in item['values']:
                    raise ReconstructionError('局部数组需要每个固定槽位恰好一次初始化。')
                if item['block'] not in (None, block):
                    raise ReconstructionError('局部数组初始化不能跨基本块。')
                item['block'], item['last_store'] = block, position
                item['values'][int(index)] = store[1]
                stores[(block, position)] = (array, int(index), store[1])
            if load and load[2] in pointers:
                loads[load[1]] = pointers[load[2]]
    def upper(value, visiting=None):
        if value.isdigit():
            return int(value)
        visiting = set() if visiting is None else visiting
        if value in visiting:
            return 0xffffffff
        body = defs.get(value, '')
        mask = re.fullmatch(r'and i32 (.+), (\d+)', body)
        shift = re.fullmatch(r'lshr i32 (.+), (\d+)', body)
        if mask:
            return int(mask[2])
        if shift and int(shift[2]) < 32:
            return upper(shift[1], visiting | {value}) >> int(shift[2])
        return 0xffffffff
    for name, (array, index) in loads.items():
        item = arrays[array]
        block, position = locations[name]
        if set(item['values']) != set(range(4)) or block != item['block'] or position <= item['last_store'] or upper(index) > 3:
            raise ReconstructionError('局部数组读取未完全初始化、跨块或索引可能越界。')
    for array in arrays:
        events = lifetime_events.get(array, [])
        if events:
            if len(events) != 2 or [e[0] for e in events] != ['start','end']:
                raise ReconstructionError('局部数组生命周期跨度不在支持范围。')
            # Optimizers may widen the lifetime to entry/exit. Require exact
            # domination/postdomination of the one block containing every use.
            successors = {b:set(re.findall(r'label %([\w.]+)',lines[-1])) for b,lines in blocks.items()}
            predecessors = {b:{p for p,children in successors.items() if b in children} for b in blocks}
            universe = set(blocks)
            dom = {b:({'0'} if b == '0' else set(universe)) for b in blocks}
            post = {b:({b} if not successors[b] else set(universe)) for b in blocks}
            changed = True
            while changed:
                changed = False
                for b in blocks:
                    d = {'0'} if b == '0' else {b}|set.intersection(*(dom[p] for p in predecessors[b])) if predecessors[b] else {b}
                    q = {b}|set.intersection(*(post[s] for s in successors[b])) if successors[b] else {b}
                    if d != dom[b] or q != post[b]:
                        dom[b],post[b],changed = d,q,True
            access_block = arrays[array]['block']
            if access_block is None or events[0][1] not in dom[access_block] or events[1][1] not in post[access_block]:
                raise ReconstructionError('局部数组生命周期没有支配全部读写。')
            used = [pos for (block,pos),(a,_,_) in stores.items() if a == array]
            used += [locations[name][1] for name,(a,_) in loads.items() if a == array]
            if any(events[0][1] == access_block and pos <= events[0][2] or events[1][1] == access_block and pos >= events[1][2] for pos in used):
                raise ReconstructionError('局部数组在有效生命周期外读写。')
    for block, lines in blocks.items():
        for position, line in enumerate(lines):
            tokens = re.findall(r'%[\w.]+', line.split(' = ', 1)[-1])
            for token in tokens:
                if (block,position) in ignored:
                    continue
                if token in lifetime_casts:
                    raise ReconstructionError('局部数组生命周期指针逃逸。')
                if token in arrays and not any(name in pointers for name in [line.split(' = ',1)[0]]):
                    raise ReconstructionError('局部数组地址逃逸或未知用途。')
                if token in pointers and (block,position) not in stores and not any(locations[name] == (block,position) for name in loads):
                    raise ReconstructionError('局部数组指针逃逸或未知用途。')
    return arrays, pointers, loads, stores, ignored
