"""Conservative HLSL reconstruction for bounded pixel, vertex and compute shaders.

Every instruction must be recognized. Unknown code is rejected, never stubbed.
The reconstructed source still needs compilation and interface verification.
"""
import re
from inspect_mmtr import reflection


class ReconstructionError(ValueError):
    pass


def shader_stage(ir):
    nodes=dict(re.findall(r'^!(\d+) = !\{(.*)\}$',ir,re.M))
    root=re.search(r'^!dx.shaderModel = !\{!(\d+)\}$',ir,re.M)
    model=re.fullmatch(r'!"(\w+)", i32 \d+, i32 \d+',nodes.get(root[1],'') if root else '')
    return model[1] if model else None


def function_body(ir):
    if len(re.findall(r'^define ', ir, re.M)) != 1:
        raise ReconstructionError('仅支持单入口、无辅助函数的 DXIL。')
    bodies = re.findall(r'^define void @([\w]+)\(\) \{\n(.*?)^\}', ir, re.M | re.S)
    if len(bodies) != 1:
        raise ReconstructionError('仅支持单入口、无辅助函数的 DXIL。')
    return bodies[0]


def shader_interface(ir):
    nodes = dict(re.findall(r'^!(\d+) = !\{(.*)\}$', ir, re.M))
    model_root = re.search(r'^!dx.shaderModel = !\{!(\d+)\}$', ir, re.M)
    model = re.fullmatch(r'!"(ps|vs|cs|ms)", i32 (\d+), i32 (\d+)',nodes.get(model_root[1],'') if model_root else '')
    if not model or model[2] != '6':
        raise ReconstructionError('当前重建支持受限SM6像素/顶点/计算着色器 PS/VS/CS。')
    stage = model[1]
    entry, body = function_body(ir)
    root = re.search(r'^!dx.entryPoints = !\{!(\d+)\}', ir, re.M)
    if not root:
        raise ReconstructionError('缺少入口元数据。')
    entry_node = nodes[root[1]]
    if stage=='ms':
        from .mesh import mesh_interface
        mesh=mesh_interface(ir)
        return entry,body,mesh['profile'],{},mesh['vertices']
    if stage=='cs':
        from .compute import compute_properties
        compute_properties(ir)
        if not re.search(r'!"'+re.escape(entry)+r'", null, (?:null|!\d+), !\d+$',entry_node):
            raise ReconstructionError('计算入口必须没有图形输入输出签名。')
        forbidden=r'@dx.op.(?:discard|sample|sampleGrad|sampleIndex|quadOp|loadInput|storeOutput)(?:\.|\()'
        if re.search(forbidden,body) or re.search(r'@dx.op.unary.f32\(i32 (?:83|84|85|86),',body):
            raise ReconstructionError('计算入口不支持此像素或图形阶段操作。')
        return entry,body,f'cs_{model[2]}_{model[3]}',{},{}
    # The first proven slice has no stage behavior flags beyond all-resources-bound
    # and native low precision (all supported values/operations remain 32-bit).
    properties = re.search(r', (null|!\d+)$', entry_node)
    if not properties:
        raise ReconstructionError('无法解析入口属性。')
    if properties[1] != 'null':
        props = nodes[properties[1][1:]]
        flags = re.fullmatch(r'i32 0, i64 (\d+)(?:, i32 5, !(\d+))?', props)
        # EarlyDepthStencil=8, RawAndStructuredBuffers=16. These must be
        # represented by an entry attribute and actual typed resource operations.
        # SV_ShadingRate and ResourceDescriptorHeap are reproduced by their
        # actual signatures/operations; strict interface checks remain required.
        # WaveOps=1<<19 is represented only by the explicitly supported wave
        # intrinsics; unknown calls still fail and SFI0 is checked after compile.
        # MinimumPrecision=32 is reconstructed with min16float, keeping actual
        # half operations and truncation/extension boundaries in DXIL.
        allowed = 16 | 32 | 256 | (1 << 19) | 8388608 | (1 << 24) | (1 << 30)
        if stage == 'ps': allowed |= 8
        if not flags or int(flags[1]) & ~allowed or stage == 'vs' and flags[2] and nodes.get(flags[2]) != 'i32 0':
            raise ReconstructionError('尚不支持此入口属性或阶段行为标志。')
    if stage == 'vs':
        # Initial vertex subset is read-only. Pixel execution/derivative
        # operations and writable memory require separate stage evidence.
        forbidden = r'@dx.op.(?:discard|sample|sampleGrad|sampleIndex|quadOp|atomicBinOp|rawBufferStore)(?:\.|\()'
        derivative = r'@dx.op.unary.f32\(i32 (?:83|84|85|86),'
        if re.search(forbidden,body) or re.search(derivative,body):
            raise ReconstructionError('顶点重建暂不支持此像素执行操作、导数或可写内存。')
    signature_node = re.search(r'!"' + re.escape(entry) + r'", !(\d+),', entry_node)
    if not signature_node:
        raise ReconstructionError('缺少签名元数据。')
    sides = nodes[signature_node[1]].split(', ')
    if len(sides) != 3 or sides[2] != 'null':
        raise ReconstructionError('不支持 patch 签名。')

    def signature(side, output=False):
        result = {}
        if side == 'null':
            return result
        for ref in re.findall(r'!(\d+)', nodes[side[1:]]):
            # Signature ID, semantic, component type, semantic kind, indices,
            # interpolation, rows, columns, packing row/column, properties.
            m = re.fullmatch(r'i32 (\d+), !"(\w+)", i8 (\d+), i8 (\d+), !(\d+), i8 (\d+), i32 (\d+), i8 (\d+), i32 (-?\d+), i8 (-?\d+), (?:null|!\d+)', nodes[ref])
            if not m or m[7] != '1' or m[3] not in ('5', '9'):
                raise ReconstructionError('不支持此签名类型、数组或形状。')
            indices = re.fullmatch(r'i32 (\d+)', nodes[m[5]])
            if not indices or not 1 <= int(m[8]) <= 4:
                raise ReconstructionError('不支持多行签名。')
            interp = {0: '', 1: 'nointerpolation', 2: 'linear', 3: 'centroid',
                      4: 'noperspective', 5: 'noperspective centroid',
                      6: 'sample', 7: 'noperspective sample'}.get(int(m[6]))
            if interp is None:
                raise ReconstructionError('未知插值模式。')
            if stage == 'vs':
                expected_kind = ({'sv_position':3,'sv_clipdistance':6,'sv_shadingrate':29} if output
                                 else {'sv_vertexid':1,'sv_instanceid':2}).get(m[2].lower(),0)
                if int(m[4]) != expected_kind or (m[2].lower().startswith('sv_') and not expected_kind):
                    raise ReconstructionError('未知顶点系统值或签名语义种类。')
                if not output and int(m[6]) != 0:
                    raise ReconstructionError('顶点输入不能使用像素插值模式。')
                if expected_kind in (1,2,29) and (m[3] != '5' or m[8] != '1' or indices[1] != '0'):
                    raise ReconstructionError('顶点系统整数值需要单个uint通道。')
            result[int(m[1])] = {'name': m[2], 'index': int(indices[1]), 'width': int(m[8]),
                                 'type': {'4': 'int', '5': 'uint', '9': 'float'}[m[3]], 'interp': interp}
        return result

    inputs, outputs = signature(sides[0]), signature(sides[1],True)
    return entry, body, f'{stage}_{model[2]}_{model[3]}', inputs, outputs


def entry_attributes(ir):
    if shader_stage(ir)=='ms':
        from .mesh import mesh_interface
        mesh=mesh_interface(ir)
        return ['[outputtopology("triangle")]','[numthreads('+', '.join(map(str,mesh['threads']))+')]']
    if shader_stage(ir)=='cs':
        from .compute import compute_properties
        _,dimensions=compute_properties(ir)
        return ['[numthreads('+', '.join(map(str,dimensions))+')]']
    nodes = dict(re.findall(r'^!(\d+) = !\{(.*)\}$', ir, re.M))
    root = re.search(r'^!dx.entryPoints = !\{!(\d+)\}', ir, re.M)
    properties = re.search(r', (null|!\d+)$', nodes[root[1]])
    flags = 0 if properties[1] == 'null' else int(re.search(r'i32 0, i64 (\d+)', nodes[properties[1][1:]])[1])
    return ['[earlydepthstencil]'] if flags & 8 else []


def interface_declarations(inputs, outputs, stage='ps'):
    lines = ['// Reconstructed from DXIL. Compile and check compatibility before replacement.',
             '// Input/output semantics and interpolation come from entry metadata.']
    for label, sig in [('ShaderInput', inputs), ('ShaderOutput', outputs)]:
        if sig:
            lines.append(f'struct {label} {{')
            for key, s in sig.items():
                t = s['type'] + (str(s['width']) if s['width'] > 1 else '')
                indexed = not s['name'].lower().startswith('sv_') or s['name'].lower() in ('sv_target','sv_clipdistance','sv_culldistance')
                semantic = s['name'] + (str(s['index']) if indexed else '')
                interpolated = label == ('ShaderInput' if stage == 'ps' else 'ShaderOutput')
                qualifier = (s['interp'] + ' ') if interpolated and s['interp'] else ''
                lines.append(f'    {qualifier}{t} field_{key} : {semantic};')
            lines.append('};')
    return lines


# 主进程拥有源码文本；heap-only程序可能没有静态资源反射，仍须交给完整材质重建器。
# 仅匹配入口正文的真实heap调用，不能因外部声明或注释选择错误路径。
def reconstruct_hlsl(ir):
    entry_body = function_body(ir)[1]
    if (reflection(ir)['resource_binding_rows'] or shader_stage(ir) in ('cs','ms')
            or '!dx.precise' in entry_body or '@dx.op.createHandleFromHeap(' in entry_body):
        from .material_hlsl import reconstruct_material
        return reconstruct_material(ir)
    entry, body, profile, inputs, outputs = shader_interface(ir)
    lines = interface_declarations(inputs, outputs,profile[:2])
    lines.extend(entry_attributes(ir))
    lines.append(f'{"ShaderOutput" if outputs else "void"} {entry}({"ShaderInput input" if inputs else ""}) {{')
    if outputs:
        lines.append('    ShaderOutput output;')
    values, assigned, count = {}, set(), 0
    from .source_map import IRSourceIndex,expand_source_map
    source_index=IRSourceIndex(ir);origins={};value_origins={}

    def field(sig, key, lane, prefix):
        if key not in sig or lane >= sig[key]['width']:
            raise ReconstructionError('签名通道越界。')
        return f'{prefix}.field_{key}' + ('.' + 'xyzw'[lane] if sig[key]['width'] > 1 else '')

    for raw in body.splitlines():
        line = raw.split(';', 1)[0].strip()
        if not line:
            continue
        count += 1
        origin=source_index.positions[('0',count-1)]
        load = re.fullmatch(r'(%\d+) = call (?:i32|float) @dx\.op\.loadInput\.(?:i32|f32)\(i32 4, i32 (\d+), i32 0, i8 (\d+), i32 undef\)', line)
        cast = re.fullmatch(r'(%\d+) = bitcast (float|i32) (%\d+) to (float|i32)', line)
        store = re.fullmatch(r'call void @dx\.op\.storeOutput\.(?:i32|f32)\(i32 5, i32 (\d+), i32 0, i8 (\d+), (?:i32|float) (%\d+)\)', line)
        if load:
            values[load[1]] = field(inputs, int(load[2]), int(load[3]), 'input')
            value_origins[load[1]]=[origin]
        elif cast and cast[3] in values and cast[2] != cast[4]:
            values[cast[1]] = f'{"asfloat" if cast[4] == "float" else "asuint"}({values[cast[3]]})'
            value_origins[cast[1]]=value_origins[cast[3]]+[origin]
        elif store and store[3] in values:
            key, lane = int(store[1]), int(store[2])
            origins[len(lines)]=value_origins[store[3]]+[origin]
            lines.append(f'    {field(outputs, key, lane, "output")} = {values[store[3]]};')
            assigned.add((key, lane))
        elif line == 'ret void':
            origins[len(lines)]=[origin]
            lines.append('    return output;' if outputs else '    return;')
        else:
            raise ReconstructionError('尚不支持的指令：' + line[:180])
    expected = {(key, lane) for key, s in outputs.items() for lane in range(s['width'])}
    if assigned != expected:
        raise ReconstructionError('输出通道未全部赋值。')
    lines.append('}')
    return {'source': '\n'.join(lines) + '\n', 'entry': entry, 'profile': profile,
            'source_map':expand_source_map(lines,origins),'origin_ir':ir,
            'instructions': count, 'limitations': '支持受限无资源直线PS/VS；编译成功后仍需接口、数据流与游戏实测。'}
