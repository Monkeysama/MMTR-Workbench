"""P5只读删除预检的会话快照与身份验证；计算由后台任务在隔离DXC中完成。

不修改Service，不写出资产；结果仅描述消费者证明，不能作为运行时删除/导出许可。
"""
import hashlib
import copy
import json
from dataclasses import dataclass

from .material_extension import current_layout
from .material_plan import dll_identity
from .mdf import MdfDocument, _public_data, master_matches
from .model import Document, EditError
from .material_removal import plan_removal_layout


# 删除必须逐 Shader 做接口改写与候选验证；旧的手动 metadata-only 操作不提供此保证。


@dataclass(frozen=True)
class RemovalEditPlan:
    """变体导出的有界删除计划适配；复用相同源身份、目标保护和双文件回滚。"""
    preview: dict

    def __getattr__(self, key):
        if key in ('source_sha256', 'mdf_sha256', 'dxc_sha256', 'source_revision', 'variant_master', 'digest'):
            return self.preview[key]
        raise AttributeError(key)

    @property
    def new(self):
        from .material_addresses import MaterialTailLayout
        value = self.preview['layout']['new']
        return MaterialTailLayout(value['parameter_bytes'], tuple(value['texture_names']), tuple(value['gpbf_names']))

    def report(self):
        return copy.deepcopy(self.preview)


def prepare_removal_preview(service, dll, actions, material_index, revision, *, variant_master=None):
    """持会话锁重开当前字节，严格核对全部已知用户与源/DXC身份，不运行原生代码。"""
    if service.doc is None or service.mdf is None:
        raise EditError('删除预检需要同时打开 MMTR 和关联 MDF。')
    index = service.mdf_selected if material_index is None else material_index
    if type(index) is not int or index < 0:
        raise EditError('删除预检需要有效材质索引。')
    source, mdf_data = service.doc.build()[0], service.mdf.build()[0]
    doc = Document(source, service.doc.source)
    mdf = MdfDocument(mdf_data, service.mdf.source)
    material = mdf.material(index)
    linked = [m for m in mdf.materials if master_matches(m['master'], str(doc.source))]
    if index not in [m['index'] for m in linked]:
        raise EditError('所选 MDF 材质未关联当前 MMTR。')
    if variant_master is not None:
        from .material_variant import validate_variant_master
        validate_variant_master(variant_master)
        if any(master_matches(variant_master, m['master']) for m in mdf.materials):
            raise EditError('独立变体不能复用已知材质 master。')
        linked = [m for m in linked if m['index'] == index]
    layout = current_layout(doc, material)
    removal = plan_removal_layout(layout, material['properties'], actions)
    for other in linked:
        if any(other.get('mmtrs') or ()):
            raise EditError('MMTRS重定向尚未覆盖，不能执行删除预检。')
        other_layout = current_layout(doc, other)
        if (other_layout != layout or
                plan_removal_layout(other_layout, other['properties'], actions).digest != removal.digest):
            raise EditError('关联材质布局不同，不能共用删除预检。')
    plan = dict(operation='removal_preview', source_revision=revision,
        source_sha256=hashlib.sha256(source).hexdigest(), mdf_sha256=hashlib.sha256(mdf_data).hexdigest(),
        dxc_sha256=dll_identity(dll), selected_material=index, target_materials=[m['index'] for m in linked],
        layout=removal.report(), external_users='not_scanned', variant_master=variant_master)
    plan['digest'] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return plan, _public_data(material)


def analyze_removal_preview(service, dll, actions, material_index, revision, *, worker_control=None, variant_master=None):
    """后台线程仅分析自身快照；父进程核对worker输出及输入身份，不提交文档。"""
    from .material_process import run_material_task
    plan, material = prepare_removal_preview(service, dll, actions, material_index, revision, variant_master=variant_master)
    result = run_material_task(service.doc, 'removal_preview', dll, material=material,
                               actions=actions, worker_control=worker_control)
    if (result['layout']['digest'] != plan['layout']['digest'] or
            dll_identity(dll) != plan['dxc_sha256']):
        raise EditError('删除预检的布局或DXC身份已改变，结果已丢弃。')
    return {**result, 'plan': plan}


def apply_material_removal(service, dll, actions, material_index=None, revision=0, *, expected_plan=None,
                           worker_control=None, variant_master=None):
    """同步全部已知用户的独立 MMTR/MDF 候选；任何编译、重开或身份检查失败均不提交。"""
    from .material_process import extend_in_worker
    from .material_addresses import MaterialTailLayout
    plan, material = prepare_removal_preview(service, dll, actions, material_index, revision,
                                              variant_master=variant_master)
    if expected_plan is not None and expected_plan != plan['digest']:
        raise EditError('删除计划身份改变，必须重新预览。')
    candidate_doc = copy.deepcopy(service.doc)
    # 重开当前 MDF 消除旧单项 patch 与结构修改叠加；用户未导出修改已包含在快照字节内。
    candidate_mdf = MdfDocument(service.mdf.build()[0], service.mdf.source)
    moves = {p['name']: p for p in plan['layout']['parameters']}
    before = {m['index']: _public_data(m) for m in candidate_mdf.materials}
    for index in plan['target_materials']:
        for name in plan['layout']['removed_parameters']:
            candidate_mdf.delete_property(index, name)
        for prop in candidate_mdf.material(index)['properties']:
            prop['offset'] = moves[prop['name']]['new_offset']
        for name in plan['layout']['removed_textures']:
            slot = next(i for i, t in enumerate(candidate_mdf.material(index)['textures']) if t['name'] == name)
            candidate_mdf.delete_texture(index, slot)
        candidate_mdf.structural_materials.add(index)
        if variant_master is not None:
            candidate_mdf.set_master(index, variant_master)
    reopened_mdf = MdfDocument(candidate_mdf.build()[0], candidate_mdf.source)
    report = extend_in_worker(candidate_doc, material, dll, operation='remove', actions=actions,
                              worker_control=worker_control)
    if report['layout']['digest'] != plan['layout']['digest']:
        raise EditError('隔离进程返回不同删除布局。')
    reopened_doc = Document(candidate_doc.build()[0], candidate_doc.source)
    expected = plan['layout']['new']
    expected = MaterialTailLayout(expected['parameter_bytes'], tuple(expected['texture_names']), tuple(expected['gpbf_names']))
    for index in plan['target_materials']:
        actual = reopened_mdf.material(index)
        if current_layout(reopened_doc, actual) != expected:
            raise EditError('删除后 MMTR/MDF 最终布局不一致。')
        old = before[index]
        old_properties = {p['name']: p for p in old['properties']}
        for prop in actual['properties']:
            previous = old_properties[prop['name']]
            if any(prop[k] != previous[k] for k in ('values', 'raw_hex', 'flags', 'byte_size')):
                raise EditError('删除改变了保留参数的值或身份。')
        public = _public_data(actual)
        if (public['gpbf'] != old['gpbf'] or public['textures'] !=
                [t for t in old['textures'] if t['name'] not in plan['layout']['removed_textures']]):
            raise EditError('删除改变了保留纹理或 GPBF 身份。')
    for m in reopened_mdf.materials:
        if m['index'] not in plan['target_materials'] and _public_data(m) != before[m['index']]:
            raise EditError('删除改变了未选中材质。')
    if (hashlib.sha256(service.doc.build()[0]).hexdigest() != plan['source_sha256'] or
            hashlib.sha256(service.mdf.build()[0]).hexdigest() != plan['mdf_sha256'] or
            dll_identity(dll) != plan['dxc_sha256']):
        raise EditError('源文档/MDF/DXC 改变，删除候选已丢弃。')
    service.doc, service.mdf = candidate_doc, candidate_mdf
    service.mdf_link_doc = service.mdf_link_report = service.mdf_migration = None
    return dict(interface_sync=report, plan=plan, target_materials=plan['target_materials'], mdf_synced=True)
