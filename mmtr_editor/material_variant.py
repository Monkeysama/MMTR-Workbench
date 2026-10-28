"""有界独立材质变体：只构建所选材质的 MMTR/MDF 候选，显式另存一对文件。

不修改调用方 Service，不自动打开、安装或扫描游戏目录。持锁/后台提交由适配层负责；
候选与目标身份分开保存，导出重核对源/目标，写失败尽力恢复已有目标并报告恢复失败。
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re

from .material_plan import MaterialEditPlan, dll_identity
from .mdf import MDF_VERSIONS, MdfDocument, _public_data, master_matches, master_root_ok, mdf_suffix_ok
from .model import Document, EditError, atomic_write


def validate_variant_master(master, mdf_version=None):
    """仅允许确定的引擎相对 MMTR 名称；不接受绝对路径、版本后缀或目录跳转。

    给出 MDF 版本时只接受该游戏的 master 根目录；未给出时接受任一已登记游戏的根目录。
    """
    if (not isinstance(master, str) or len(master) > 32767 or '\\' in master
            or any(ord(c) < 32 for c in master) or ':' in master
            or not master_root_ok(master, mdf_version)
            or not master.casefold().endswith('.mmtr')
            or any(part in ('', '.', '..') for part in master.split('/'))):
        roots = MDF_VERSIONS[mdf_version][1] if mdf_version in MDF_VERSIONS else '/'.join(r for _g, r in MDF_VERSIONS.values())
        raise EditError(f'变体 master 必须是 {roots} 下的 .mmtr 相对路径，不带版本或目录跳转。')
    try:
        if len(master.encode('utf-16-le')) > 65534:
            raise ValueError('too long')
    except (UnicodeError, ValueError) as exc:
        raise EditError('变体 master 不是有效的有界 UTF-16 文本。') from exc
    return master


@dataclass(frozen=True)
class VariantDestination:
    """一个显式输出目标；预览保存原目标内容身份，执行前拒绝覆盖后来出现的文件。"""
    path: str
    prior_sha256: str | None


@dataclass(frozen=True)
class VariantPlan:
    """布局摘要、材质范围和输出位置共同参与摘要，不把路径当作可随意替换的参数。"""
    edit: MaterialEditPlan
    mmtr: VariantDestination
    mdf: VariantDestination
    known_users: tuple[tuple[int, str, str], ...]
    selected_material: int
    source_mmtr: str
    source_mdf: str

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, ensure_ascii=True,
                                         allow_nan=False).encode()).hexdigest()

    def report(self):
        return {**asdict(self), 'digest': self.digest, 'scope': 'independent_variant',
                'external_users': 'not_scanned', 'game_tested': False}


@dataclass(frozen=True)
class VariantCandidate:
    """仅供本地事务使用的完整候选；协议只能返回报告，不序列化载荷。"""
    plan: VariantPlan
    mmtr_bytes: bytes
    mdf_bytes: bytes
    report: dict
    mmtr_sha256: str
    mdf_sha256: str


def _fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _destinations(service, mmtr_path, mdf_path, master, *, overwrite):
    validate_variant_master(master, service.mdf.version)
    paths = [service.check_write_target(path) for path in (mmtr_path, mdf_path)]
    version = re.search(r'\.mmtr(\.\d+)$', str(service.doc.source), re.I)
    if (version is None or not paths[0].as_posix().casefold().endswith((master+version[1]).casefold())
            or not master_matches(master, str(paths[0]))):
        raise EditError('MMTR 输出位置必须保留 master 相对目录和当前资源版本后缀。')
    if not mdf_suffix_ok(paths[1], service.mdf.version):
        raise EditError(f'MDF 输出必须以 .mdf2.{service.mdf.version} 结尾。')
    if paths[0] == paths[1] or (paths[0].exists() and paths[1].exists() and paths[0].samefile(paths[1])):
        raise EditError('变体 MMTR/MDF 不能指向相同文件或硬链接。')
    result = []
    for path in paths:
        if not path.parent.is_dir():
            raise EditError(f'输出目录不存在：{path.parent}')
        if path.exists() and (not path.is_file() or not overwrite):
            raise EditError(f'输出目标已存在或不是普通文件：{path}')
        result.append(VariantDestination(str(path), _fingerprint(path)))
    return tuple(result)


def prepare_material_variant(service, dll, actions, record_index, mmtr_path, mdf_path, master,
                             source_revision=0, *, overwrite=False, operation='extend'):
    """只规划一个所选材质；已知其它使用者完整列出，未扫描范围不解释为无人使用。"""
    from .material_service import prepare_material_batch
    if service.doc is None or service.mdf is None:
        raise EditError('独立变体需要先打开 MMTR 与 MDF。')
    mmtr, mdf = _destinations(service, mmtr_path, mdf_path, master, overwrite=overwrite)
    if operation == 'remove':
        from .material_removal_service import prepare_removal_preview, RemovalEditPlan
        report, _ = prepare_removal_preview(service, dll, actions, service.mdf_selected, source_revision,
                                            variant_master=master)
        edit = RemovalEditPlan(report)
    elif operation == 'extend':
        edit, _, _ = prepare_material_batch(service, dll, actions, record_index, source_revision,
                                             variant_master=master)
    else:
        raise EditError('未知变体操作。')
    snapshot = MdfDocument(service.mdf.build()[0], service.mdf.source)
    users = tuple((m['index'], m['name'], m['master']) for m in snapshot.materials
                  if master_matches(m['master'], str(service.doc.source)))
    return VariantPlan(edit, mmtr, mdf, users, service.mdf_selected,
                       str(service.doc.source), str(service.mdf.source))


def _check_source(service, dll, plan):
    if (service.doc is None or service.mdf is None or service.mdf_selected != plan.selected_material
            or str(service.doc.source) != plan.source_mmtr or str(service.mdf.source) != plan.source_mdf
            or hashlib.sha256(service.doc.build()[0]).hexdigest() != plan.edit.source_sha256
            or hashlib.sha256(service.mdf.build()[0]).hexdigest() != plan.edit.mdf_sha256
            or dll_identity(dll) != plan.edit.dxc_sha256):
        raise EditError('变体源文档、所选材质或 DXC 已改变，必须重新规划。')


def build_material_variant(service, dll, actions, record_index, plan, *, worker_control=None):
    """独立快照上迁移接口；源编辑始终保留，验证未选材质的完整公开语义未改变。"""
    from .material_service import apply_material_batch
    _check_source(service, dll, plan)
    candidate = deepcopy(service)
    from .material_removal_service import RemovalEditPlan, apply_material_removal
    if isinstance(plan.edit, RemovalEditPlan):
        report = apply_material_removal(candidate, dll, actions, plan.selected_material, plan.edit.source_revision,
            expected_plan=plan.edit.digest, worker_control=worker_control, variant_master=plan.edit.variant_master)
    else:
        report = apply_material_batch(candidate, dll, actions, record_index, plan.edit.source_revision,
            expected_plan=plan.edit.digest, worker_control=worker_control, variant_master=plan.edit.variant_master)
    mmtr_bytes, mdf_bytes = candidate.doc.build()[0], candidate.mdf.build()[0]
    reopened = MdfDocument(mdf_bytes, candidate.mdf.source)
    original = MdfDocument(service.mdf.build()[0], service.mdf.source)
    for before, after in zip(original.materials, reopened.materials):
        if before['index'] != plan.selected_material and _public_data(before) != _public_data(after):
            raise EditError('独立变体意外改变了未选中的 MDF 材质。')
    _verify_pair(plan, mmtr_bytes, mdf_bytes)
    _check_source(service, dll, plan)
    return VariantCandidate(plan, mmtr_bytes, mdf_bytes, report,
                            hashlib.sha256(mmtr_bytes).hexdigest(), hashlib.sha256(mdf_bytes).hexdigest())


def _verify_pair(plan, mmtr_bytes, mdf_bytes):
    """独立重开核对 master、布局及容器完整性；不把反射通过当作游戏效果证明。"""
    from .material_extension import current_layout
    doc = Document(mmtr_bytes, Path(plan.mmtr.path))
    mdf = MdfDocument(mdf_bytes, Path(plan.mdf.path))
    material = mdf.material(plan.selected_material)
    if (material['master'] != plan.edit.variant_master or not master_matches(material['master'], plan.mmtr.path)
            or current_layout(doc, material) != plan.edit.new
            or doc.integrity['name_hash_mismatches'] or doc.integrity['shader_trailer_mismatches']):
        raise EditError('独立变体重开后 master、布局或 MMTR 完整性不一致。')


def export_material_variant(service, dll, candidate):
    """显式导出完整一对文件；普通写失败回滚不承诺跨文件进程崩溃原子性。"""
    plan = candidate.plan
    if (hashlib.sha256(candidate.mmtr_bytes).hexdigest() != candidate.mmtr_sha256
            or hashlib.sha256(candidate.mdf_bytes).hexdigest() != candidate.mdf_sha256):
        raise EditError('变体候选载荷身份不一致。')
    _check_source(service, dll, plan)
    _verify_pair(plan, candidate.mmtr_bytes, candidate.mdf_bytes)
    paths = [service.check_write_target(d.path) for d in (plan.mmtr, plan.mdf)]
    for path, destination in zip(paths, (plan.mmtr, plan.mdf)):
        if str(path) != destination.path or _fingerprint(path) != destination.prior_sha256:
            raise EditError('变体输出目标在预览后改变，必须重新规划。')
    if paths[0] == paths[1] or (all(p.exists() for p in paths) and paths[0].samefile(paths[1])):
        raise EditError('变体目标出现相同文件或硬链接。')
    backups = [p.read_bytes() if p.exists() else None for p in paths]
    payloads = [candidate.mmtr_bytes, candidate.mdf_bytes]
    touched = []
    try:
        for i, (path, data) in enumerate(zip(paths, payloads)):
            touched.append(i)
            atomic_write(path, data)
        _verify_pair(plan, paths[0].read_bytes(), paths[1].read_bytes())
        if any(path.read_bytes() != data for path, data in zip(paths, payloads)):
            raise EditError('变体写入后的文件内容与已验证候选不一致。')
    except Exception as failure:
        rollback_errors = []
        for i in reversed(touched):
            try:
                if backups[i] is None:
                    paths[i].unlink(missing_ok=True)
                else:
                    atomic_write(paths[i], backups[i])
            except Exception as exc:
                rollback_errors.append(f'{paths[i]}: {exc}')
        if rollback_errors:
            raise EditError('变体导出失败且部分目标恢复失败：'+'; '.join(rollback_errors)) from failure
        raise
    return {'scope': 'independent_variant', 'plan_digest': plan.digest,
            'files': [{'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
                      for path, data in zip(paths, payloads)],
            'source_state': 'preserved', 'game_tested': False}
