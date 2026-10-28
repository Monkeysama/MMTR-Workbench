"""隔离 worker 内执行有界删除；全量证明后编译一个候选，不管理文件或会话锁。"""
import copy
import hashlib
import re

from inspect_mmtr import reflection
from .material_addresses import material_reads
from .material_markers import material_marker_reads
from .material_extension import bodies, chunks, current_layout, reflection_container, replace_chunks
from .material_removal import plan_removal_layout, prove_shader_removal, remap_shader_reads
from .model import Document, EditError, interface_chunks
from .removal_writer import patch_removal_declarations


def _reads(ir):
    """函数名与SSA联合标识取值；编译后逐字段核对而不依赖全模块同名局部变量。"""
    bindings = '\n'.join(line for line in ir.splitlines() if line.startswith('; '))+'\n'
    result = {}
    for function in re.findall(r'^define .*?^}', ir, re.M | re.S):
        name = re.search(r'@([^ (]+)\(', function)[1]
        for read in material_reads(bindings+function, include_static=True):
            for ex in read['extracts']:
                result[name, ex['ssa']] = (ex['offset'], read['kind'], read['stride'])
    return result


def _guards(ir, layout, components):
    """按函数及 SSA 记录已证明的 guard；编译后不能少检查、换来源或复用旧计数。"""
    bindings = '\n'.join(line for line in ir.splitlines() if line.startswith('; '))+'\n'
    result = []
    for function in re.findall(r'^define .*?^}', ir, re.M | re.S):
        name = re.search(r'@([^ (]+)\(', function)[1]
        source = bindings+function
        _, guards = material_marker_reads(source, layout, components, material_reads(source, include_static=True))
        result.extend((name, g['extract'], g['comparison']) for g in guards)
    return result


def remove_material_interface(document, material, dll, actions, *, progress=None):
    """已用/未知字段拒绝；分别重建执行模块和STAT，核对保留字段、签名、资源和布局。"""
    from .dxil_assembly import DxilAssembly
    from .material_plan import dll_identity
    from .structure import parse_structure
    if any(material.get('mmtrs') or ()):
        raise EditError('MMTRS 重定向未覆盖，不能执行删除。')
    progress = progress or (lambda *args: None)
    identity = dll_identity(dll)
    source = document.build()[0]
    strict = Document(source, document.source)
    info = strict.info
    structure = parse_structure(source, document.source)
    if any(d.severity == 'error' or d.code == 'resource_version_unverified' for d in structure.diagnostics):
        raise EditError('源结构预检失败，不能执行删除。')
    layout = plan_removal_layout(current_layout(strict, material), material['properties'], actions)
    candidate = copy.deepcopy(document)
    compiler = DxilAssembly(dll)
    reports, reflections, cache = [], {}, {}
    try:
        for position, shader in enumerate(info['shaders']):
            index = shader['index']
            progress('removing_shaders', position, len(info['shaders']))
            original = strict.shader_bytes(index)
            key = hashlib.sha256(original).hexdigest(), shader['stage']
            if key not in cache:
                ir = compiler.disassemble(original)
                prove_shader_removal(ir, layout)
                before = reflection(ir)
                edited, migration = remap_shader_reads(ir, layout)
                expected_reads = _reads(edited)
                expected_guards = _guards(edited, layout.new, layout.new_components)
                edited = patch_removal_declarations(edited, layout, before)
                reflected_ir = compiler.disassemble(reflection_container(original))
                if re.search(r'^define ', reflected_ir, re.M):
                    reflected_ir, _ = remap_shader_reads(reflected_ir, layout)
                reflected_ir = patch_removal_declarations(reflected_ir, layout, before)
                executable, _ = compiler.assemble(edited)
                reflected, _ = compiler.assemble(reflected_ir)
                payload, _ = compiler.validate(replace_chunks(executable, {'STAT': chunks(reflected)['STAT']}))
                actual = compiler.disassemble(payload)
                after = reflection(actual)
                if bodies(edited) != bodies(actual) or interface_chunks(original) != interface_chunks(payload):
                    raise EditError('删除后 Shader 原计算或签名发生意外变化。')
                if _reads(actual) != expected_reads:
                    raise EditError('删除后保留字段读取地址/实例步长与计划不一致。')
                if _guards(actual, layout.new, layout.new_components) != expected_guards:
                    raise EditError('删除后材质布局标记未按新 MDF 计数完整保留。')
                for _, (_, kind, stride) in _reads(actual).items():
                    if kind == 'instance' and stride and stride['bytes'] != layout.new.parameter_bytes:
                        raise EditError('删除后实例步长不一致。')
                expected_bindings = [row for row in before['resource_binding_rows']
                    if row.split()[0] not in layout.removed_textures]
                # SRV内部range编号可收缩，寄存器/space/count/格式必须保留。
                def bindings(rows):
                    return [tuple(row.split()[:-3]+row.split()[-2:]) for row in rows]
                if bindings(after['resource_binding_rows']) != bindings(expected_bindings):
                    raise EditError('删除后保留资源的绑定寄存器或类型改变。')
                if before['structured_buffers'] != after['structured_buffers']:
                    raise EditError('删除意外改变结构化缓冲。')
                for cb in before['cbuffers']:
                    updated = next((b for b in after['cbuffers'] if b['name'] == cb['name']), None)
                    expected = copy.deepcopy(cb)
                    if cb['name'] == 'UserMaterial' and layout.removed_parameters:
                        moves = {p.name: p for p in layout.parameters}
                        expected['size'] = layout.new.parameter_bytes
                        expected['variables'] = [v for v in expected['variables'] if v['name'].removeprefix('VAR_') in moves]
                        for variable in expected['variables']:
                            variable['offset'] = moves[variable['name'].removeprefix('VAR_')].new_offset
                    if updated != expected:
                        raise EditError('删除后常量缓冲反射与字段身份不一致。')
                cache[key] = payload, after, len(expected_guards)
            payload, after, markers = cache[key]
            candidate.replace_shader(index, payload, allow_interface_change=True,
                note={'material_removal': True, 'dxc_validated': True})
            reflections[index] = hashlib.sha256(payload).digest(), after
            reports.append(dict(index=index, sha256=hashlib.sha256(payload).hexdigest(), material_markers=markers))
        operation = dict(kind='remove_material_interface', layout=layout.report(),
            shader_hashes={str(i): hashlib.sha256(candidate.shader_bytes(i)).hexdigest() for i in reflections},
            redirects=[[r, f, t] for (r, f), t in sorted(candidate.redirects.items())])
        candidate.metadata_edits.append(operation)
        progress('validating_removal', 0, 1)
        rebuilt = candidate.build()[0]
        reopened = Document(rebuilt, document.source)
        if reopened.integrity['name_hash_mismatches'] or reopened.integrity['shader_trailer_mismatches']:
            raise EditError('删除候选容器完整性验证失败。')
        if any(d.severity == 'error' for d in parse_structure(rebuilt, document.source).diagnostics):
            raise EditError('删除候选结构或资源计数验证失败。')
        validation = candidate.validate_metadata_interfaces(dll, _in_worker=True, _reflections=reflections)
        if not validation['valid']:
            raise EditError('删除候选接口验证失败：'+str(validation['issues'][0]))
        if dll_identity(dll) != identity:
            raise EditError('删除执行期间 DXC 身份改变。')
    finally:
        compiler.close()
    document.replacements, document.replacement_notes = candidate.replacements, candidate.replacement_notes
    document.metadata_edits = candidate.metadata_edits
    progress('validated', 1, 1)
    return dict(layout=layout.report(), validation=validation, shader_reports=reports,
                changed_shaders=len(reports), shaders_analyzed=len(cache), dxc_sha256=identity,
                game_tested=False, coverage=dict(scope='bounded_dxil_consumers', deletion_supported=True))
