"""显式关联布局验证：只读当前 MMTR/MDF 快照，在隔离 DXC 中逐 Shader 核对。

复用有界读取证明的恒等布局，不迁移或修补任何字节；未知访问使覆盖失败。
"""
import hashlib
import re

from inspect_mmtr import reflection
from .material_addresses import material_reads
from .material_extension import current_layout
from .material_markers import marker_value
from .material_plan import dll_identity
from .material_removal import ParameterMove, RemovalLayout, prove_shader_removal
from .material_textures import native_texture_reads
from .mdf import MdfDocument, _public_data, master_matches
from .model import Document, EditError
from .structure import parse_structure


def identity_layout(document, material):
    """MDF 实际属性证明恒等映射；padding 不被当作属性，分量数不取自块大小。"""
    layout = current_layout(document, material)
    parameters, components = [], 0
    for prop in sorted(material['properties'], key=lambda p: p['offset']):
        size, count = prop['byte_size'], prop['count']
        if size not in (4, 8, 12, 16) or type(count) is not int or count != size//4:
            raise EditError('关联 MDF 属性不在已验证的 float1..4 分量范围。')
        parameters.append(ParameterMove(prop['name'], prop['offset'], prop['offset'], size))
        components += count
    marker_value(layout, components)
    return RemovalLayout(layout, layout, tuple(parameters), (), (), components, components)


def validate_material_layout(document, material, compiler):
    """工作进程内验证全部载荷；共享载荷复用分析，每个 Shader 索引仍计入覆盖。"""
    identity = identity_layout(document, material)
    info = document.current_info()
    members = next(cb['members'] for cb in info['metadata']['cbuffers'] if cb['name'] == 'UserMaterial')
    expected = {m['name']: (m['byte_offset'], m['byte_size']) for m in members}
    issues, cache = [], {}
    passed, markers, native_reads = 0, 0, 0
    for shader in info['shaders']:
        index = shader['index']
        payload = document.shader_bytes(index)
        key = hashlib.sha256(payload).hexdigest()
        if key not in cache:
            try:
                compiler.validate(payload)
                ir = compiler.disassemble(payload)
                if not re.search(r'^define ', ir, re.M):
                    raise EditError('未识别到可验证的 Shader 函数。')
                reflected = reflection(ir)
                for cb in reflected['cbuffers']:
                    if cb['name'] != 'UserMaterial':
                        continue
                    actual = {}
                    for variable in cb['variables']:
                        width = re.fullmatch(r'float([1-4])?', variable['type'])
                        if width is None:
                            raise EditError('UserMaterial 反射含未覆盖类型。')
                        actual[variable['name'].removeprefix('VAR_')] = (
                            variable['offset'], 4*int(width[1] or 1))
                    if actual != expected or cb['size'] != identity.old.parameter_bytes:
                        raise EditError('UserMaterial 反射与当前 MMTR/MDF 布局不一致。')
                proof = prove_shader_removal(ir, identity)
                module = re.sub(r'^define .*?^}', '', ir, flags=re.M | re.S)
                native = 0
                for function in re.findall(r'^define .*?^}', ir, re.M | re.S):
                    source = module+'\n'+function
                    native += len(native_texture_reads(source, identity.old,
                        material_reads(source, include_static=True, header_bytes=identity.old.header_bytes), reflected))
                cache[key] = (proof['material_markers'], native, None)
            except (ValueError, KeyError) as exc:
                cache[key] = (0, 0, str(exc))
        guard_count, native_count, problem = cache[key]
        if problem is not None:
            issues.append({'kind': 'associated_layout', 'shader_index': index, 'problem': problem})
        else:
            passed += 1
            markers += guard_count
            native_reads += native_count
    return {'status': 'failed' if issues else 'passed', 'scope': 'associated_layout', 'valid': not issues,
        'checked_shaders': len(info['shaders']), 'passed_shaders': passed,
        'failed_shaders': len(issues), 'unique_payloads': len(cache),
        'checked_records': len(info['records']), 'checked_markers': markers,
        'checked_native_texture_reads': native_reads, 'issues': issues, 'layout': identity.report(),
        'coverage': {'scope': 'bounded_dxil_consumers', 'complete': not issues,
                     'external_users': 'not_scanned'}, 'game_tested': False}


def validate_associated_layout(document, mdf, dll):
    """父进程严格重开两个快照并核对已知使用者；DXC/输入变化后丢弃只读结果。"""
    if mdf is None:
        raise EditError('关联布局验证需要同时打开 MMTR 和关联 MDF。')
    source, mdf_data = document.build()[0], mdf.build()[0]
    strict, materials = Document(source, document.source), MdfDocument(mdf_data, mdf.source)
    linked = [m for m in materials.materials if master_matches(m['master'], str(strict.source))]
    if not linked:
        raise EditError('当前 MDF 没有使用此 MMTR 的材质，无法验证关联布局。')
    base = {'status': 'failed', 'scope': 'associated_layout', 'valid': False,
        'checked_shaders': 0, 'checked_records': strict.info['record_count'], 'checked_markers': 0,
        'checked_materials': len(linked), 'checked_native_texture_reads': 0,
        'issues': [], 'metadata_edits': len(document.metadata_edits),
        'source_sha256': hashlib.sha256(source).hexdigest(),
        'mdf_sha256': hashlib.sha256(mdf_data).hexdigest(),
        'target_materials': [m['index'] for m in linked],
        'coverage': {'scope': 'bounded_dxil_consumers', 'complete': False,
                     'external_users': 'not_scanned'}, 'game_tested': False}
    structure = parse_structure(source, document.source)
    for diagnostic in structure.diagnostics:
        if diagnostic.severity == 'error' or diagnostic.code == 'resource_version_unverified':
            base['issues'].append({'kind': 'structure', 'problem': diagnostic.message})
    identities = []
    for material in linked:
        try:
            if any(material.get('mmtrs') or ()):
                raise EditError('MMTRS 重定向尚未覆盖，无法验证关联布局。')
            identities.append(identity_layout(strict, material))
            if identities[-1] != identities[0]:
                raise EditError('关联材质的属性分量或纹理/GPBF 布局不同。')
        except (ValueError, KeyError) as exc:
            base['issues'].append({'kind': 'material_layout', 'material_index': material['index'],
                                   'problem': str(exc)})
    if strict.integrity['name_hash_mismatches'] or strict.integrity['shader_trailer_mismatches']:
        base['issues'].append({'kind': 'integrity', 'problem': '当前 MMTR 名称或 Shader 载荷哈希不一致。'})
    if base['issues']:
        return base
    from .material_process import run_material_task
    dxc_sha = dll_identity(dll)
    result = run_material_task(strict, 'associated_layout', dll, material=_public_data(linked[0]))
    if (document.build()[0] != source or mdf.build()[0] != mdf_data or dll_identity(dll) != dxc_sha):
        raise EditError('关联布局验证期间 MMTR/MDF 或 DXC 身份改变，结果已丢弃。')
    return {**base, **result, 'dxc_sha256': dxc_sha}
