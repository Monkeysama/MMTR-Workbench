"""P5 删除布局与覆盖证明：纯计划，不修改文档、运行 DXC 或写出文件。

按字段身份映射保留参数/纹理/GPBF，拒绝读取删除项或未归属填充。
消费者证明只允许已覆盖的函数与资源访问；后续执行仍须独立重编译、重解析和复查。
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import re

from .material_addresses import MaterialTailLayout, material_reads
from .material_markers import material_marker_reads, relocate_material_markers
from .material_usage import shader_input_reads
from .dxil_sources import parse
from .model import EditError


@dataclass(frozen=True)
class ParameterMove:
    """一个保留的 float1..4 参数；同名身份对应明确的源/目标字节范围。"""
    name: str
    old_offset: int
    new_offset: int
    byte_size: int


@dataclass(frozen=True)
class RemovalLayout:
    """参数按16B行约束重新打包，尾部按原顺序删除；未知字节没有隐式映射。"""
    old: MaterialTailLayout
    new: MaterialTailLayout
    parameters: tuple[ParameterMove, ...]
    removed_parameters: tuple[str, ...]
    removed_textures: tuple[str, ...]
    old_components: int
    new_components: int

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, ensure_ascii=True).encode()).hexdigest()

    def report(self):
        """仅包含字段身份与布局，不保存IR或可绕过复查的执行许可。"""
        return {'digest': self.digest, **asdict(self)}

    def offset_in(self, offset, *, instance=False):
        if type(offset) is not int or offset < 0 or offset % 4:
            raise EditError('删除迁移只接受非负4字节对齐读取。')
        if offset < self.old.parameter_bytes:
            for parameter in self.parameters:
                if parameter.old_offset <= offset and offset+4 <= parameter.old_offset+parameter.byte_size:
                    return parameter.new_offset+offset-parameter.old_offset
            raise EditError(f'读取偏移 {offset} 属于已删除参数或未证明的填充字节。')
        if instance:
            raise EditError('参数实例读取超出原参数区，不能解释为纹理/GPBF。')
        old_fields, new_fields = self.old.fields(), self.new.fields()
        name = next((name for name, position in old_fields.items() if position == offset), None)
        if name is None or name not in new_fields:
            raise EditError(f'读取偏移 {offset} 属于已删除纹理或未知尾部字段。')
        return new_fields[name]


def plan_removal_layout(old, properties, actions):
    """完整属性布局上计算一次最终收缩；不接受GPBF删除、空UserMaterial或模糊动作。"""
    if old.parameter_bytes <= 0 or old.parameter_bytes % 16 or old.parameter_bytes > 0xffff:
        raise EditError('删除需要已验证的非空16B对齐参数布局。')
    if not isinstance(actions, (list, tuple)) or not 1 <= len(actions) <= 100:
        raise EditError('删除需要1..100个明确动作。')
    if len(set(old.texture_names+old.gpbf_names)) != len(old.texture_names+old.gpbf_names):
        raise EditError('原材质尾部名称重复。')
    ordered = sorted(properties, key=lambda p: p['offset'])
    names, end = set(), 0
    for parameter in ordered:
        name, start, size = parameter['name'], parameter['offset'], parameter['byte_size']
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_]\w*', name, re.ASCII)
                or name in names or type(start) is not int or type(size) is not int
                or size not in (4, 8, 12, 16) or start < end or start % 4
                or start+size > old.parameter_bytes or start//16 != (start+size-1)//16):
            raise EditError('原参数名称、范围或float1..4布局不在删除支持范围。')
        if type(parameter.get('count', size//4)) is not int or parameter.get('count', size//4) != size//4:
            raise EditError('MDF 参数分量数与实际字节范围不一致。')
        names.add(name)
        end = start+size
    removed, selected = {'parameter': [], 'texture': []}, set()
    for action in actions:
        if not isinstance(action, dict) or set(action) != {'action', 'name'}:
            raise EditError('删除动作只接受action与name。')
        kind, name = action['action'], action['name']
        if kind not in removed or not isinstance(name, str):
            raise EditError('删除仅支持parameter或texture。')
        key = kind, name
        if key in selected or name not in (names if kind == 'parameter' else old.texture_names):
            raise EditError('删除目标重复或不存在。')
        selected.add(key)
        removed[kind].append(name)
    moves, cursor = [], 0
    for parameter in ordered:
        if parameter['name'] in removed['parameter']:
            continue
        size = parameter['byte_size']
        if cursor % 16+size > 16:
            cursor = (cursor+15)&~15
        moves.append(ParameterMove(parameter['name'], parameter['offset'], cursor, size))
        cursor += size
    if not moves:
        raise EditError('本档删除保留至少一个UserMaterial参数。')
    # 只删纹理时不重排参数；避免与本次无关的参数空洞收缩。
    if not removed['parameter']:
        moves = [ParameterMove(p['name'], p['offset'], p['offset'], p['byte_size']) for p in ordered]
    size = (cursor+15)&~15 if removed['parameter'] else old.parameter_bytes
    new = MaterialTailLayout(size, tuple(t for t in old.texture_names if t not in removed['texture']), old.gpbf_names,
                             old.header_bytes)
    # MDF 标量数独立于参数块大小；删去填充行内的属性也必须改变布局标记。
    old_components = sum(p.get('count', p['byte_size']//4) for p in ordered)
    new_components = sum(p.byte_size//4 for p in moves)
    return RemovalLayout(old, new, tuple(moves), tuple(removed['parameter']), tuple(removed['texture']),
                         old_components, new_components)


def shader_removal_reads(ir):
    """加强已有读取器供删除使用；未知外部调用或无法迁移的访问均保留为阻塞。"""
    reads = shader_input_reads(ir)
    errors = list(reads['errors'])
    functions = re.findall(r'^define .*?^}', ir, flags=re.M | re.S)
    bindings = '\n'.join(line for line in ir.splitlines() if line.startswith('; '))
    for function in functions:
        for call in re.findall(r'\b(?:call|invoke)\b[^\n]*', function):
            target = re.search(r'@([\w.$]+)\(', call)
            if not target or not target[1].startswith('dx.op.'):
                errors.append(('all', '存在未证明的函数调用；本档不支持跨函数参数传递。'))
        try:
            # 删除还需迁移静态UserMaterial地址；较窄类型等未支持形式不能仅凭usage通过。
            material_reads(bindings+'\n'+name_numeric_locals(function), include_static=True)
        except (ValueError, KeyError) as exc:
            errors.append(('all', f'删除寻址未覆盖：{exc}'))
    return {**reads, 'errors': list(dict.fromkeys(errors))}


def prove_shader_removal(ir, layout):
    """只有全部读取可归属到保留字段才返回本Shader证明；不把unused统计当作删除许可。"""
    reads = shader_removal_reads(ir)
    if reads['errors']:
        raise EditError('删除覆盖不足：'+reads['errors'][0][1])
    bindings = '\n'.join(line for line in ir.splitlines() if line.startswith('; '))
    record_words, parameter_words, markers = set(), set(), 0
    for function in re.findall(r'^define .*?^}', ir, flags=re.M | re.S):
        source = bindings+'\n'+function
        _, uses, _ = parse(source)
        ordinary, guards = material_marker_reads(source, layout.old, layout.old_components,
                                                 material_reads(source, include_static=True))
        markers += len(guards)
        for read in ordinary:
            if read['stride'] is not None and read['stride']['bytes'] != layout.old.parameter_bytes:
                raise EditError('实例步长与原参数区尺寸不一致。')
            for extract in read['extracts']:
                if not uses.get(extract['ssa']):
                    continue
                instance = read['kind'] in ('instance', 'static')
                layout.offset_in(extract['offset'], instance=instance)
                (parameter_words if instance else record_words).add(extract['offset'])
    if set(layout.removed_textures) & reads['textures']:
        raise EditError('待删除纹理仍有静态资源句柄消费者。')
    return {'layout_digest': layout.digest, 'record_words': sorted(record_words),
            'parameter_words': sorted(parameter_words), 'material_markers': markers,
            'named_textures': sorted(reads['textures']), 'scope': 'bounded_dxil_consumers',
            'requires_candidate_validation': True, 'game_tested': False}


def remap_shader_reads(ir, layout):
    """逐函数重写已证明的保留字段与实例步长；仅返回IR候选，声明与STAT须由执行器同步。"""
    from .material_addresses import relocate_material_reads
    proof = prove_shader_removal(ir, layout)
    bindings = '\n'.join(line for line in ir.splitlines() if line.startswith('; '))+'\n'
    reports = []
    def function(match):
        source = bindings+name_numeric_locals(match[0])+'\n'
        edited, ordinary, guards = relocate_material_markers(source, layout.old, layout.new,
            layout.old_components, layout.new_components-layout.old_components,
            material_reads(source, include_static=True))
        edited, report = relocate_material_reads(edited, layout.old, layout.new, reads=ordinary, relocation=layout)
        report['material_markers'] = len(guards)
        reports.append(report)
        return edited[len(bindings):].removesuffix('\n')
    edited = re.sub(r'^define .*?^}', function, ir, flags=re.M | re.S)
    return edited, {'proof': proof, 'functions': reports, 'declarations_updated': False,
                    'requires_candidate_validation': True}


def name_numeric_locals(function):
    """删除指令前为LLVM隐式编号命名，覆盖块标签/phi引用；避免删除后编号断层。"""
    prefix = 'mmtrRemovalSSA'
    while '%'+prefix in function or re.search(r'^'+prefix, function, re.M):
        prefix += '_'
    labels = set(re.findall(r'^(?:; <label>:|\s*)(\d+):?', function, re.M))
    definitions = set(re.findall(r'^\s*%(\d+) = ', function, re.M)) | labels
    header, body = function.split('{', 1)
    definitions.update(re.findall(r'%(\d+)\b', header))
    # 未标记的入口块也可被phi/branch引用；唯一未知编号只能指向它。
    references = set(re.findall(r'%(\d+)\b', body))
    implicit_entry = references-definitions
    if len(implicit_entry) > 1:
        raise EditError('数字SSA存在多个无法证明的隐式块或定义。')
    if implicit_entry:
        body = '\n'+prefix+next(iter(implicit_entry))+':'+body
    text = header+'{'+body
    text = re.sub(r'^; <label>:(\d+):?[^\n]*', lambda m: prefix+m[1]+':', text, flags=re.M)
    text = re.sub(r'^(\s*)(\d+):', lambda m: m[1]+prefix+m[2]+':', text, flags=re.M)
    return re.sub(r'%(\d+)\b', lambda m: '%'+prefix+m[1], text)


def analyze_material_removal(document, material, actions, disassemble, *, progress=None):
    """worker内逐载荷分析，逐Shader保存覆盖身份；没有消费者证明时不授予候选资格。"""
    from .material_extension import current_layout
    if any(material.get('mmtrs') or ()):
        raise EditError('MMTRS重定向尚未覆盖，不能执行删除预检。')
    info = document.current_info()
    removal = plan_removal_layout(current_layout(document, material, info), material['properties'], actions)
    seen, rows, failures = {}, [], []
    shaders = info['shaders']
    for count, shader in enumerate(shaders):
        payload = document.shader_bytes(shader['index'])
        digest = hashlib.sha256(payload).hexdigest()
        if digest not in seen:
            try:
                proof = prove_shader_removal(disassemble(payload), removal)
                seen[digest] = {'eligible': True, 'proof': proof}
            except Exception as exc:
                seen[digest] = {'eligible': False, 'error': str(exc) or type(exc).__name__}
        outcome = seen[digest]
        if outcome['eligible']:
            proof = outcome['proof']
            outcome = {**outcome, 'proof': {k: v for k, v in proof.items()
                        if k not in ('record_words', 'parameter_words', 'named_textures')},
                       'record_word_count': len(proof['record_words']),
                       'parameter_word_count': len(proof['parameter_words']),
                       'named_texture_count': len(proof['named_textures'])}
        row = {'shader_index': shader['index'], 'stage': shader['stage'],
               'sha256': digest, **outcome}
        rows.append(row)
        if not row['eligible']:
            failures.append({'shader_index': shader['index'], 'error': row['error']})
        if progress is not None:
            progress('analyzing_removal', count+1, len(shaders))
    return {'eligible': bool(rows) and not failures, 'layout': removal.report(),
            'shader_count': len(shaders), 'shaders_analyzed': len(seen),
            'shaders': rows, 'failures': failures,
            'coverage': {'scope': 'bounded_dxil_consumers', 'deletion_supported': False,
                         'all_shaders_analyzed': bool(rows) and not failures,
                         'requires_candidate_validation': True}, 'game_tested': False}
