"""Exact bounded group-shared float arrays and scalar accesses for CS source.

No constant folding or store-to-load forwarding: another lane may write a read
location. Semantic equivalence must separately retain every event and barrier.
"""
import re
from .reconstruct import ReconstructionError


def analyze_shared_memory(ir,blocks):
    arrays={};pointers={};loads={};stores={};barriers=set()
    for line in ir.splitlines():
        if not line.startswith('@') or 'addrspace(3)' not in line:continue
        m=re.fullmatch(r'(@(?:"[^"\n]+"|[\w.$]+)) = (?:(?:internal )?addrspace\(3\) global \[(\d+) x float\] undef|external addrspace\(3\) global \[(\d+) x float\]), align 4',line)
        if not m or not 1<=int(m[2] or m[3])<=8192 or m[1] in arrays:
            raise ReconstructionError('仅支持有界未初始化的groupshared float数组，align4。')
        arrays[m[1]]=dict(name=f'shared_float_{len(arrays)}',size=int(m[2] or m[3]),type='float')
    if sum(a['size']*4 for a in arrays.values())>32768:
        raise ReconstructionError('groupshared总长度超出32KB。')
    for block,lines in blocks.items():
        for position,line in enumerate(lines):
            p=re.fullmatch(r'(%[\w.]+) = getelementptr(?: inbounds)? \[(\d+) x float\], \[\2 x float\] addrspace\(3\)\* (@(?:"[^"\n]+"|[\w.$]+)), i32 0, i32 (.+)',line)
            if p:
                if p[3] not in arrays or arrays[p[3]]['size']!=int(p[2]):
                    raise ReconstructionError('groupshared地址声明或数组长度不一致。')
                pointers[p[1]]=(p[3],p[4]);continue
            if 'getelementptr' in line and 'addrspace(3)' in line:
                raise ReconstructionError('groupshared指针形态不受支持。')
    for block,lines in blocks.items():
        for position,line in enumerate(lines):
            location=(block,position)
            load=re.fullmatch(r'(%[\w.]+) = load float, float addrspace\(3\)\* (%[\w.]+), align 4(?:, !tbaa !\d+)?',line)
            store=re.fullmatch(r'store float (.+), float addrspace\(3\)\* (%[\w.]+), align 4(?:, !tbaa !\d+)?',line)
            if load and load[2] in pointers:loads[load[1]]=pointers[load[2]]
            elif store and store[2] in pointers:stores[location]=(*pointers[store[2]],store[1])
            elif re.search(r'\b(?:store|load)\b.+addrspace\(3\)',line):
                raise ReconstructionError('groupshared load/store类型、指针或对齐不受支持。')
            if '@dx.op.barrier' in line:
                if line!='call void @dx.op.barrier(i32 80, i32 9)':
                    raise ReconstructionError('仅支持GroupMemoryBarrierWithGroupSync模式9。')
                barriers.add(location)
            defined=line.split(' = ',1)[0] if ' = ' in line else None
            for name in re.findall(r'%[\w.]+',line.split(' = ',1)[-1]):
                if name in pointers and not (load and load[2]==name or store and store[2]==name):
                    raise ReconstructionError('groupshared指针逃逸或未知用途。')
            for name in arrays:
                if name in line and defined not in pointers:
                    raise ReconstructionError('groupshared全局地址逃逸或未知用途。')
    return arrays,pointers,loads,stores,barriers
