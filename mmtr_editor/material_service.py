"""MMTR/MDF 共享批量事务；调用方串行持有 Service，候选验证完成后一起提交。

不负责协议、GUI 或文件安装。所有已知同 master 材质必须同步，失败保留原有编辑。
"""
import copy
import hashlib

from .material_extension import current_layout
from .material_plan import dll_identity, plan_material_edit
from .material_process import extend_in_worker
from .mdf import MdfDocument, master_matches
from .model import Document, EditError


# 先用重开的当前字节严格证明所有关联材质，防止旧单项未同步状态被批次静默继承。
def prepare_material_batch(service, dll, actions, record_index=None, source_revision=0):
    if service.doc is None or service.mdf is None:
        raise EditError('批量扩展需要先打开 MMTR 与关联 MDF。')
    if type(source_revision) is not int or source_revision < 0:
        raise EditError('source_revision 必须是非负整数。')
    doc, mdf = service.doc, service.mdf
    strict = Document(doc.build()[0], doc.source) if doc.dirty else doc
    mdf_bytes = mdf.build()[0]
    snapshot = MdfDocument(mdf_bytes, mdf.source)
    linked = [m for m in snapshot.materials if master_matches(m['master'], str(doc.source))]
    if not linked or service.mdf_selected not in [m['index'] for m in linked]:
        raise EditError('所选 MDF 材质未关联当前 MMTR。')
    layouts = [current_layout(strict, m) for m in linked]
    if any(layout != layouts[0] for layout in layouts):
        raise EditError('多个关联材质的纹理/GPBF布局不同，不能共用一次接口扩展。')
    material = next(m for m in linked if m['index'] == service.mdf_selected)
    context = {'source_revision': source_revision, 'mdf_sha256': hashlib.sha256(mdf_bytes).hexdigest(),
               'target_materials': tuple(m['index'] for m in linked)}
    return plan_material_edit(doc, material, dll, actions, record_index, **context), material, context


# 全批次只处理一个候选 MMTR/MDF；MDF 提前预构建，坏默认值或结构错误不浪费 DXC 工作。
def apply_material_batch(service, dll, actions, record_index=None, source_revision=0, expected_plan=None):
    plan, material, context = prepare_material_batch(service, dll, actions, record_index, source_revision)
    if expected_plan is not None and plan.digest != expected_plan:
        raise EditError('计划身份已改变，必须重新规划。')
    candidate_doc, candidate_mdf = copy.deepcopy(service.doc), copy.deepcopy(service.mdf)
    for index in plan.target_materials:
        for parameter in plan.parameters:
            candidate_mdf.add_property(index, parameter.name, list(parameter.values), offset=parameter.byte_offset)
        for texture in plan.textures:
            candidate_mdf.add_texture(index, texture.name, texture.path)
    mdf_bytes = candidate_mdf.build()[0]
    reopened_mdf = MdfDocument(mdf_bytes, candidate_mdf.source)
    report = extend_in_worker(candidate_doc, material, dll, actions=actions, record_index=record_index,
                              plan_context=context, expected_plan=plan.digest)
    reopened_doc = Document(candidate_doc.build()[0], candidate_doc.source)
    for index in plan.target_materials:
        if current_layout(reopened_doc, reopened_mdf.material(index)) != plan.new:
            raise EditError('批量候选 MDF 与最终 MMTR 布局不一致。')
    if (hashlib.sha256(service.doc.build()[0]).hexdigest() != plan.source_sha256 or
            hashlib.sha256(service.mdf.build()[0]).hexdigest() != plan.mdf_sha256 or
            dll_identity(dll) != plan.dxc_sha256):
        raise EditError('源文档/MDF/DXC 身份改变，拒绝提交过期批次。')
    service.doc, service.mdf = candidate_doc, candidate_mdf
    service.mdf_link_doc = service.mdf_link_report = service.mdf_migration = None
    return {'interface_sync': report, 'target_materials': list(plan.target_materials),
            'plan': plan.report(), 'mdf_synced': True}
