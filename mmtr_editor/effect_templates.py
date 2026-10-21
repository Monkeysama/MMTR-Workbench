"""P6 有界效果模板；只生成显式接口与 Shader 合同，不猜测 DXIL 或状态缓存编码。"""
from dataclasses import dataclass
import hashlib
import json

from .model import EditError


@dataclass(frozen=True)
class EffectTemplate:
    """一个需要调用方提供 Shader 编译结果的最小效果模板。"""
    name: str
    description: str
    actions: tuple[dict, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    shader_contract: str

    def report(self):
        value = {'template': self.name, 'description': self.description,
                 'actions': self.actions, 'inputs': self.inputs, 'outputs': self.outputs,
                 'shader_contract': self.shader_contract, 'game_tested': False,
                 'state_editing': 'unsupported', 'requires_compiled_shader': True}
        value['digest'] = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        return value


TEMPLATES = {
    'emission_multiplier': EffectTemplate(
        'emission_multiplier',
        '将已有像素输出乘以显式强度；保留原受光、透明和其它通道计算。',
        (
            {'action': 'parameter', 'name': 'MMTR_EmissionStrength', 'byte_size': 4, 'values': [1.0]},
            {'action': 'texture', 'name': 'MMTR_EmissionMap', 'path': 'systems/rendering/NullBlack.tex'},
        ),
        ('MMTR_EmissionStrength', 'MMTR_EmissionMap', '原像素输出'),
        ('最终像素输出',),
        'Shader 必须显式读取 MMTR_EmissionStrength 与 MMTR_EmissionMap，并将结果合并到原像素输出；不得移除原受光/透明分支。',
    ),
}


def effect_template(name):
    """返回封闭模板报告；未知模板、任意节点图和渲染状态写回一律拒绝。"""
    if not isinstance(name, str) or name not in TEMPLATES:
        raise EditError('未知或未验证的效果模板；当前仅支持 emission_multiplier。')
    return TEMPLATES[name].report()
