"""Parse supported DXIL instruction attachments without discarding precision."""
import re
from .reconstruct import ReconstructionError


def precise_instruction(line, metadata):
    """Return syntax without a *validated* attachment and its precise result.

    DXC prints precise on floating DXIL calls as an i32-one metadata node.
    Keep the SSA identity so HLSL emission can enforce the constraint. Unknown
    forms remain errors rather than being removed by a generic metadata regex.
    """
    if '!dx.precise' not in line:
        return line, None
    m = re.fullmatch(r'(%[\w.]+) = (call (?:float|half) @dx\.op\.[\w.]+\(.*\)), !dx\.precise !(\d+)', line)
    if not m or metadata.get(m[3]) != 'i32 1':
        raise ReconstructionError('不支持的 dx.precise 附加标记或元数据；未移除精度约束。')
    return f'{m[1]} = {m[2]}', m[1]
