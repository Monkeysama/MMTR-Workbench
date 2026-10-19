"""Restricted compute entry metadata; no shader execution."""
import re
from .reconstruct import ReconstructionError


def compute_properties(ir):
    nodes=dict(re.findall(r'^!(\d+) = !\{(.*)\}$',ir,re.M))
    root=re.search(r'^!dx.entryPoints = !\{!(\d+)\}$',ir,re.M)
    entry=nodes.get(root[1],'') if root else ''
    reference=re.search(r', !(\d+)$',entry)
    props=nodes.get(reference[1],'') if reference else ''
    m=re.fullmatch(r'(?:i32 0, i64 (\d+), )?i32 4, !(\d+)(?:, i32 5, !(\d+))?',props)
    # Int64Ops (bit20) is reproduced only by actual typed i64 instructions;
    # shader calls/types still need translation and strict SFI0 validation.
    allowed=16|256|8388608|(1<<19)|(1<<20)|(1<<30)
    if not m or int(m[1] or 0) & ~allowed or m[3] and nodes.get(m[3])!='i32 0':
        raise ReconstructionError('未知计算入口属性、行为标志或线程组属性。')
    dimensions=re.fullmatch(r'i32 (\d+), i32 (\d+), i32 (\d+)',nodes.get(m[2],''))
    if not dimensions:raise ReconstructionError('缺少完整numthreads。')
    x,y,z=map(int,dimensions.groups())
    if not (1<=x<=1024 and 1<=y<=1024 and 1<=z<=64 and x*y*z<=1024):
        raise ReconstructionError('numthreads超出SM6计算阶段范围。')
    return int(m[1] or 0),(x,y,z)


COMPUTE_PARAMETERS=('uint3 dispatchThreadID : SV_DispatchThreadID, uint3 groupID : SV_GroupID, '
                    'uint3 groupThreadID : SV_GroupThreadID, uint groupIndex : SV_GroupIndex')
