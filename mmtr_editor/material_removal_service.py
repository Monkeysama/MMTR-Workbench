"""P5只读删除预检的会话快照与身份验证；计算由后台任务在隔离DXC中完成。

不修改Service，不写出资产；结果仅描述消费者证明，不能作为运行时删除/导出许可。
"""
import hashlib
import json

from .material_extension import current_layout
from .material_plan import dll_identity
from .mdf import MdfDocument, _public_data, master_matches
from .model import Document, EditError
from .material_removal import plan_removal_layout


def prepare_removal_preview(service, dll, actions, material_index, revision):
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
        layout=removal.report(), external_users='not_scanned')
    plan['digest'] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return plan, _public_data(material)


def analyze_removal_preview(service, dll, actions, material_index, revision, *, worker_control=None):
    """后台线程仅分析自身快照；父进程核对worker输出及输入身份，不提交文档。"""
    from .material_process import run_material_task
    plan, material = prepare_removal_preview(service, dll, actions, material_index, revision)
    result = run_material_task(service.doc, 'removal_preview', dll, material=material,
                               actions=actions, worker_control=worker_control)
    if (result['layout']['digest'] != plan['layout']['digest'] or
            dll_identity(dll) != plan['dxc_sha256']):
        raise EditError('删除预检的布局或DXC身份已改变，结果已丢弃。')
    return {**result, 'plan': plan}
