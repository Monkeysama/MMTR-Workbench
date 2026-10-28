"""JSON-lines RPC for Tauri; only this process owns the editable document."""
import csv
import io
import json
from pathlib import Path
import sys
import os
import traceback

from .model import Document, EditError, atomic_write
from .mdf import MdfDocument, MdfError, master_matches, mdf_suffix_ok
from .migration import build_migration_report
from .dxc import reflection_differences
from .dxc_process import compile_hlsl,disassemble_shader,DxcProcessError
from .reconstruct import reconstruct_hlsl
from . import __version__
from .bounded_cache import ByteLRU


class Service:
    def __init__(self):
        self.doc = None
        self.compiled = None
        self.compile_cache = ByteLRU(8, 16 * 1024 * 1024)
        self.mdf = None
        self.mdf_selected = None
        self.mdf_link_report = None
        self.mdf_link_doc = None
        self.mdf_migration = None
        # 桌面会话在导出前更新全部标签的源路径；纯路径可随候选安全复制。
        self.workspace_sources = ()

    def snapshot(self):
        d = self.doc
        if not d:
            return None
        info = d.current_info()
        return {**{k: info[k] for k in ['source','size','sha256','record_count','shader_count','stages','shader_models','candidate_groups']},
                'dirty': d.dirty, 'integrity': d.integrity,
                'changes': {'shaders': list(d.replacements), 'names': len(d.renames),
                            'references': len(d.redirects), 'metadata': len(d.metadata_edits)},
                'shaders': [{'index':s['index'],'stage':s['stage'],'model':s['shader_model'],'size':s['size'],
                             'currentSize':len(d.shader_bytes(s['index'])),'offset':s['offset'],
                             'changed':s['index'] in d.replacements,'passes':s['pass_names']}
                            for s in info['shaders']],
                'passes':[{'index':r['index'],'group':r['index']//info['header_u16_06'],
                           'name':d.shader_name(r['name_offset']),
                           'refs':[{'field':x['field'],'stage':x['stage'], 'shader':d.effective_shader(r['index'],x['field'])}
                                   for x in r['shader_references']]} for r in info['records']],
                'metadata':self.metadata()}

    def metadata(self):
        d, rows, seen = self.doc, [], set()
        info = d.current_info()
        for kind, entries in info['metadata'].items():
            for e in entries:
                rows.append({'kind':kind,'buffer':'','name':e['name'],
                             'pointer':e['name_offset'],'capacity':len(e['name']),
                             'offset':e['offset'],'byteOffset':None,'size':e.get('buffer_size')})
                if kind == 'cbuffers':
                    for m in e['members']:
                        if m['offset'] in seen:
                            continue
                        seen.add(m['offset'])
                        rows.append({'kind':'member','buffer':e['name'],
                                     'name':m['name'],'pointer':m['name_offset'],
                                     'capacity':len(m['name']),'offset':m['offset'],
                                     'byteOffset':m['byte_offset'],'size':m['byte_size']})
        return rows

    def mdf_snapshot(self):
        if self.mdf is None:
            return None
        linked = self.mdf_link_doc or self.doc
        info = None
        if linked:
            info = Document(linked.build()[0], linked.source).info if linked.dirty else linked.info
        self.mdf_link_report = self.mdf.binding_report(self.mdf_selected, info) if self.mdf_selected is not None else None
        result = self.mdf.snapshot(self.mdf_selected)
        # 每个 MDF 材质独立比较 master；当前打开的 MMTR 只对路径匹配的材质生效。
        current_source = info.get('source', '') if info else ''
        for material in result['materials']:
            material['mmtr_linked'] = bool(current_source and master_matches(material['master'], current_source))
        if result.get('selected') is not None:
            result['selected']['mmtr_linked'] = bool(current_source and master_matches(result['selected']['master'], current_source))
        result["binding"] = self.mdf_link_report
        result['migration'] = self.mdf_migration
        return result

    def mdf_write_snapshot(self):
        if self.mdf is None:
            raise MdfError("请先打开 MDF。")
        data, report = self.mdf.build()
        return data, report

    # 关联MDF证明纹理和GPBF顺序；只读取当前事务快照，不修改其它材质或游戏文件。
    def _material_for_extension(self):
        from .material_extension import current_layout
        if self.mdf is None:
            raise EditError('请先打开使用当前MMTR的MDF，以确认纹理和GPBF布局。')
        snapshot = MdfDocument(self.mdf.build()[0], self.mdf.source)
        linked = [m for m in snapshot.materials if master_matches(m['master'], str(self.doc.source))]
        if not linked:
            raise EditError('当前MDF没有使用此MMTR的材质，不能自动迁移接口。')
        info = self.doc.current_info()
        layouts = [current_layout(self.doc, material, info) for material in linked]
        if any(layout != layouts[0] for layout in layouts):
            raise EditError('多个关联材质的纹理/GPBF布局不同，不能共用一次接口扩展。')
        return next((m for m in linked if m['index'] == self.mdf_selected), linked[0])

    def _require_mdf_interface_name(self, kind: str, name: str) -> dict | None:
        """在 MMTR 已加载时限制 MDF 新接口只能使用现有反射名称。"""
        if self.doc is None:
            return
        info = self.doc.current_info() if hasattr(self.doc, 'current_info') else self.doc.info
        if kind == 'property':
            rows = [x for x in info.get('metadata', {}).get('cbuffers', []) if x.get('name') == 'UserMaterial']
            member = next((m for row in rows for m in row.get('members', []) if m.get('name') == name), None)
            if member is None:
                raise MdfError('当前版本尚未支持新增 MMTR Shader 参数接口；请先使用已有 UserMaterial 成员。')
            return member
        elif kind == 'texture':
            names = {x.get('name') for x in info.get('metadata', {}).get('srvs', [])}
            if name not in names:
                raise MdfError('当前版本尚未支持新增 MMTR Shader 纹理接口；请先使用已有 SRV 名称。')
        return None

    def mdf_select(self, index):
        if self.mdf is None:
            raise MdfError("请先打开 MDF。")
        self.mdf.material(index)
        self.mdf_selected = index
        self.mdf_migration = None
        return self.mdf_snapshot()

    # 导出目标预检独立于写入，供MCP在多文件事务开始前检查全部源文件保护。
    def check_write_target(self, path):
        path = Path(path).resolve()
        for source in self.workspace_sources:
            source = Path(source)
            if path == source or (path.exists() and source.exists() and path.samefile(source)):
                raise EditError('不能覆盖任一已打开标签的源文件。')
        if self.doc and (path == self.doc.source or (path.exists() and self.doc.source.exists() and path.samefile(self.doc.source))):
            raise EditError('不能覆盖打开的原始 MMTR。')
        if self.mdf and self.mdf.source and (path == self.mdf.source or (path.exists() and path.samefile(self.mdf.source))):
            raise EditError('不能覆盖打开的原始 MDF。')
        if self.mdf_migration:
            for key in ('source_mmtr', 'target_mmtr'):
                source = Path(self.mdf_migration[key]['source']).resolve()
                if path == source or (path.exists() and source.exists() and path.samefile(source)):
                    raise EditError('不能覆盖迁移报告使用的源或目标 MMTR。')
        return path

    def safe_write(self, path, data):
        atomic_write(self.check_write_target(path), data)

    def decode(self, dll, payload):
        return disassemble_shader(dll,payload)

    def handle(self, req):
        op = req['op']
        if op == 'ping':
            default = Path(os.environ.get('MMTR_DXC_PATH', ''))
            return {'version':__version__,'dxc':str(default.resolve()) if default.is_file() else '',
                    'material_interface_extensions': True, 'mcp_stdio': True}
        if op == 'open':
            doc = Document.open(req['path'])
            self.doc = doc
            self.mdf_link_doc = None
            self.mdf_migration = None
            return self.snapshot()
        if op == 'compile':
            self.compiled = None
            from .material_plan import dll_identity
            import hashlib
            # 首编仍在隔离子进程；复用键包含完整源码、编译参数及 DLL 内容身份。
            identity = dll_identity(req['dll'])
            key = (str(Path(req['dll']).resolve()), identity,
                   hashlib.sha256(req['source'].encode('utf-8')).hexdigest(), req['entry'], req['profile'])
            result = self.compile_cache.get(key)
            if result is None:
                result = compile_hlsl(req['dll'],req['source'],req['entry'],req['profile'])
                if dll_identity(req['dll']) != identity:
                    raise EditError('编译期间 DXC 内容已改变，请重新编译。')
                self.compile_cache.put(key, result, sum(sys.getsizeof(x) for x in (*key, *result)) + 512)
            elif dll_identity(req['dll']) != identity:
                raise EditError('编译期间 DXC 内容已改变，请重新编译。')
            payload,warnings,ir=result
            self.compiled = payload
            return {'size':len(payload),'warnings':warnings,'ir':ir}
        if op == 'export_compiled':
            if self.compiled is None:
                raise EditError('请先编译 HLSL。')
            self.safe_write(req['path'],self.compiled)
            return {'path':req['path']}
        if op == 'snapshot':
            return self.snapshot()
        if op == 'mmtr_inspect_structure':
            if self.doc is None:
                raise EditError('请先打开 MMTR。')
            from .structure import document_structure
            return document_structure(self.doc).report(req.get('view', 'summary'), req.get('index'),
                                                      int(req.get('offset', 0)), int(req.get('limit', 40)))
        if op in ('material_plan', 'material_apply'):
            from .material_service import prepare_material_batch, apply_material_batch
            arguments = (self, req['dll'], req.get('actions'), req.get('record_index'), req.get('source_revision', 0))
            if op == 'material_plan':
                return prepare_material_batch(*arguments)[0].report()
            return apply_material_batch(*arguments, expected_plan=req.get('expected_plan'))
        if op == 'mmtr_validate_interfaces':
            if self.doc is None:
                raise EditError('请先打开 MMTR。')
            scope = req.get('scope', 'metadata_delta')
            if scope == 'associated_layout':
                from .material_validation import validate_associated_layout
                return validate_associated_layout(self.doc, self.mdf, str(req.get('dll', '')))
            if scope != 'metadata_delta':
                raise EditError('未知接口验证范围。')
            return self.doc.validate_metadata_interfaces(str(req.get('dll', '')))
        if op == 'mmtr_sync_compiled_interface':
            if self.doc is None:
                raise EditError('请先打开 MMTR。')
            if self.compiled is None:
                raise EditError('请先编译 HLSL。')
            report = self.doc.sync_compiled_interface(int(req['index']), self.compiled, str(req.get('dll', '')))
            return {**self.snapshot(), 'interface_sync': report}
        if op == 'mmtr_sync_compiled_interfaces':
            if self.doc is None:
                raise EditError('请先打开 MMTR。')
            items = req.get('items')
            if not isinstance(items, list) or not items:
                raise EditError('批量接口同步需要 items 列表。')
            payloads = {}
            for item in items:
                if not isinstance(item, dict):
                    raise EditError('批量接口项格式无效。')
                index = int(item['index'])
                if item.get('use_compiled'):
                    if self.compiled is None:
                        raise EditError(f'Shader #{index} 没有当前编译结果。')
                    payloads[index] = self.compiled
                else:
                    path = str(item.get('path', ''))
                    if not path:
                        raise EditError(f'Shader #{index} 缺少 DXBC 路径。')
                    payloads[index] = Path(path).read_bytes()
            report = self.doc.sync_compiled_interfaces(payloads, str(req.get('dll', '')))
            return {**self.snapshot(), 'interface_sync': report}
        if op in ('mmtr_add_cbuffer_member', 'mmtr_delete_cbuffer_member', 'mmtr_add_resource', 'mmtr_delete_resource'):
            if self.doc is None:
                raise EditError('请先打开 MMTR。')
            if op == 'mmtr_add_cbuffer_member':
                # 兼容旧前端曾发送的 camelCase 字段，同时统一转换为模型 snake_case 参数。
                byte_size = req.get('byte_size', req.get('byteSize'))
                byte_offset = req.get('byte_offset', req.get('byteOffset'))
                if byte_size is None or byte_offset is None:
                    raise EditError('新增 MMTR 参数缺少 byte_size 或 byte_offset。')
                if req.get('auto_sync'):
                    from .material_process import extend_in_worker
                    if req.get('cbuffer', 'UserMaterial') != 'UserMaterial':
                        raise EditError('自动材质扩展只支持UserMaterial。')
                    if not req.get('dll'):
                        raise EditError('请先设置DXC路径，再新增材质接口。')
                    report = extend_in_worker(self.doc, self._material_for_extension(), req['dll'],
                        parameter={'name': str(req['name']), 'byte_size': int(byte_size), 'byte_offset': int(byte_offset)})
                    return {**self.snapshot(), 'interface_sync': report}
                self.doc.add_cbuffer_member(req.get('cbuffer', 'UserMaterial'), str(req['name']), int(byte_size), int(byte_offset))
            elif op == 'mmtr_delete_cbuffer_member':
                self.doc.remove_cbuffer_member(req.get('cbuffer', 'UserMaterial'), str(req['name']))
            elif op == 'mmtr_add_resource':
                if req.get('auto_sync'):
                    from .material_process import extend_in_worker
                    if req['kind'] != 'srvs':
                        raise EditError('自动材质扩展只支持Texture2D SRV。')
                    if not req.get('dll'):
                        raise EditError('请先设置DXC路径，再新增材质接口。')
                    report = extend_in_worker(self.doc, self._material_for_extension(), req['dll'],
                        texture=str(req['name']), record_index=int(req['record_index']))
                    return {**self.snapshot(), 'interface_sync': report}
                self.doc.add_resource(str(req['kind']), int(req['record_index']), str(req['name']))
            else:
                self.doc.remove_resource(str(req['kind']), int(req['record_index']), str(req['name']))
            return self.snapshot()
        if op == 'mmtr_material_usage':
            if self.doc is None or self.mdf is None:
                raise EditError('读取统计需要同时打开 MMTR 和关联 MDF。')
            if not req.get('dll'):
                raise EditError('请先设置DXC路径，再统计材质读取。')
            from .mdf import _public_data
            from .material_process import run_material_task
            index = int(req['material_index'])
            material = self.mdf.material(index)
            if not master_matches(material['master'], str(self.doc.source)):
                raise EditError('所选 MDF 材质未关联当前 MMTR。')
            return run_material_task(self.doc, 'usage', req['dll'], material=_public_data(material))
        if op == 'mdf_snapshot':
            return self.mdf_snapshot()
        if op == 'mdf_open':
            new_mdf = MdfDocument.open(req['path'])
            selected = req.get('material_index', 0) if new_mdf.materials else None
            if selected is not None:
                new_mdf.material(selected)
            self.mdf, self.mdf_selected = new_mdf, selected
            self.mdf_migration = None
            return self.mdf_snapshot()
        if op == 'mdf_select':
            return self.mdf_select(req['material_index'])
        if op in ('mdf_set_property', 'mdf_set_property_components'):
            if self.mdf is None:
                raise MdfError("请先打开 MDF。")
            if op == 'mdf_set_property_components':
                self.mdf.set_property_components(req['material_index'], req['name'], req['components'])
            else:
                self.mdf.set_property(int(req['material_index']), str(req['name']), [float(x) for x in req['values']])
            self.mdf_migration = None
            self.mdf_selected = int(req['material_index'])
            self.mdf_link_report = self.mdf.binding_report(self.mdf_selected, self.doc.current_info() if self.doc else None)
            return self.mdf_snapshot()
        if op == 'mdf_set_flags':
            if self.mdf is None:
                raise MdfError("请先打开 MDF。")
            self.mdf.set_flags(int(req['material_index']), req.get('flags_a'), req.get('flags_b'))
            self.mdf_migration = None
            self.mdf_selected = int(req['material_index'])
            self.mdf_link_report = self.mdf.binding_report(self.mdf_selected, self.doc.current_info() if self.doc else None)
            return self.mdf_snapshot()
        if op in ('mdf_set_setting', 'mdf_set_texture', 'mdf_set_master'):
            if self.mdf is None:
                raise MdfError('请先打开 MDF。')
            index = req['material_index']
            if op == 'mdf_set_setting':
                self.mdf.set_setting(index, req['field'], req['value'])
            elif op == 'mdf_set_master':
                self.mdf.set_master(index, req['path'])
                self.mdf_link_doc = None
            else:
                self.mdf.set_texture(index, req['slot'], req['path'])
            self.mdf_selected = index
            self.mdf_migration = None
            return self.mdf_snapshot()
        if op in ('mdf_add_property', 'mdf_delete_property', 'mdf_add_texture', 'mdf_delete_texture'):
            if self.mdf is None:
                raise MdfError('请先打开 MDF。')
            index = int(req['material_index'])
            if op == 'mdf_add_property':
                name = str(req['name'])
                member = self._require_mdf_interface_name('property', name)
                values = req.get('values')
                values = [float(x) for x in values]
                expected_size = member.get('byte_size') if member else None
                if expected_size is not None and expected_size != len(values) * 4:
                    raise MdfError(f'MMTR 参数 {name} 需要 {expected_size // 4} 个 float 值。')
                self.mdf.add_property(index, name, values, int(req.get('flags', 0)), member.get('byte_offset') if member else None)
            elif op == 'mdf_delete_property':
                self.mdf.delete_property(index, str(req['name']))
            elif op == 'mdf_add_texture':
                name = str(req['name'])
                self._require_mdf_interface_name('texture', name)
                self.mdf.add_texture(index, name, str(req['path']))
            else:
                self.mdf.delete_texture(index, int(req['slot']))
            self.mdf_selected = index
            self.mdf_migration = None
            self.mdf_link_report = self.mdf.binding_report(index, self.doc.current_info() if self.doc else None)
            return self.mdf_snapshot()
        if op == 'mdf_reset':
            if self.mdf is None:
                raise MdfError("请先打开 MDF。")
            self.mdf.reset()
            self.mdf_migration = None
            self.mdf_link_report = self.mdf.binding_report(self.mdf_selected, self.doc.current_info() if self.doc and self.mdf_selected is not None else None) if self.mdf_selected is not None else None
            return self.mdf_snapshot()
        if op == 'mdf_export':
            data, report = self.mdf_write_snapshot()
            if not mdf_suffix_ok(req['path'], self.mdf.version):
                raise MdfError(f'导出路径必须以 .mdf2.{self.mdf.version} 结尾。')
            self.safe_write(req['path'], data)
            self.mdf.saved_sha256 = report['sha256']
            return report
        if op == 'mdf_link':
            if self.mdf is None:
                raise MdfError("请先打开 MDF。")
            if req.get('mmtr_path'):
                linked = self.doc if self.doc and Path(req['mmtr_path']).resolve() == self.doc.source else Document.open(req['mmtr_path'])
                self.mdf_link_doc = linked
            else:
                self.mdf_link_report = self.mdf.binding_report(self.mdf_selected if self.mdf_selected is not None else int(req.get('material_index', 0)), self.doc.current_info() if self.doc else None)
            return self.mdf_snapshot()
        if op == 'mdf_migration':
            if self.mdf is None or self.mdf_selected is None:
                raise MdfError('请先选择 MDF 材质。')
            source = Document.open(req['source_path'])
            target = Document.open(req['target_path'])
            report = build_migration_report(self.mdf.material(self.mdf_selected), source.info, target.info)
            report['mdf'] = self.mdf.build()[1]
            report['shared_material_count'] = sum(m['master'] == report['source_master'] for m in self.mdf.materials)
            self.mdf_migration = report
            return self.mdf_snapshot()
        if op == 'mdf_report':
            result = self.mdf_snapshot()
            if result is None:
                raise MdfError('请先打开 MDF。')
            self.safe_write(req['path'], json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'))
            return {'path': req['path']}
        if self.doc is None:
            raise EditError('请先打开 MMTR。')
        d = self.doc
        if op == 'disassemble':
            return {'ir':self.decode(req['dll'],d.shader_bytes(req['index']))}
        if op == 'reconstruct':
            payload=d.shader_bytes(req['index'])
            result=reconstruct_hlsl(self.decode(req['dll'],payload))
            from inspect_mmtr import sha256
            return {**result,'origin_shader_index':req['index'],'origin_shader_sha256':sha256(payload),
                    'origin_document':str(d.source)}
        if op == 'export_shader':
            self.safe_write(req['path'],d.shader_bytes(req['index']))
            return {'path':req['path']}
        if op in ['replace','apply_compiled']:
            payload = Path(req['path']).read_bytes() if op == 'replace' else self.compiled
            if payload is None:
                raise EditError('请先编译 HLSL。')
            index, expert = req['index'], bool(req.get('expert',False))
            differences = d.interface_differences(index,payload)
            old = d.info['shaders'][index]
            left = self.decode(req['dll'],d.original[old['offset']:old['trailer_offset']])
            right = self.decode(req['dll'],payload)
            reflection_changes = reflection_differences(left,right)
            if (differences or reflection_changes) and not expert:
                raise EditError('接口或资源反射不同：'+', '.join(differences+reflection_changes)+'。请检查代码，或明确开启专家选项。')
            d.replace_shader(index,payload,allow_interface_change=expert,
                             note={'dxc_disassembly_ok':True,'reflection_differences':reflection_changes})
        elif op == 'restore_shader':
            d.replacements.pop(req['index'],None)
            d.replacement_notes.pop(req['index'],None)
        elif op == 'rename':
            d.rename(req['pointer'],req['name'])
        elif op == 'redirect':
            source = d.effective_shader(req['record'],req['field'])
            if source is None:
                raise EditError('此通道没有指定阶段。')
            payload = d.shader_bytes(req['target'])
            changes = reflection_differences(self.decode(req['dll'],d.shader_bytes(source)),self.decode(req['dll'],payload))
            if changes and not req.get('expert',False):
                raise EditError('目标资源反射不同：'+', '.join(changes))
            d.redirect(req['record'],req['field'],req['target'],allow_interface_change=req.get('expert',False))
        elif op == 'reset':
            d.reset()
        elif op == 'export':
            if d.metadata_edits:
                validation = d.validate_metadata_interfaces(str(req.get('dll', '')))
                if not validation['valid']:
                    first = validation['issues'][0]
                    detail = first.get('problem') or f"{first.get('kind')} {first.get('name', '')}"
                    raise EditError(f'MMTR metadata 与 DXIL 接口不一致，禁止导出：{detail}')
            return d.export(req['path'])
        elif op == 'report':
            _, report = d.build()
            report['original_integrity'] = d.integrity
            self.safe_write(req['path'],json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
            return report
        elif op == 'export_layout':
            stream = io.StringIO(newline='')
            rows = self.metadata()
            writer = csv.DictWriter(stream,fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
            self.safe_write(req['path'],stream.getvalue().encode('utf-8-sig'))
            return {'path':req['path']}
        else:
            raise EditError('未知操作：'+op)
        return self.snapshot()


class _RpcContext:
    """单个桌面标签的文档上下文；锁、revision 与后台任务均只属于该上下文。"""

    def __init__(self, context_id: str):
        from threading import RLock
        from .material_jobs import MaterialJobs
        self.context_id = context_id
        self.service = Service()
        self.lock = RLock()
        self.revision = 0
        self.exported_revision = 0
        self.dll = self.service.handle({'op': 'ping'})['dxc']
        self.jobs = MaterialJobs(self)

    def invalidate(self):
        """兼容任务提交的失效通知；桌面派生视图由快照重建。"""
        return None

    def unsaved(self):
        """关闭保护只比较已导出的 MMTR revision 和 MDF 实际构建哈希。"""
        mdf = self.service.mdf
        return bool(self.service.doc and self.service.doc.dirty and self.revision != self.exported_revision) or bool(
            mdf and mdf.build()[1]['sha256'] != mdf.saved_sha256)

    def pending(self):
        """任务锁只用于读取状态；关闭时必须在释放锁后回收线程。"""
        from .material_jobs import TERMINAL
        with self.jobs.lock:
            return any(job.state not in TERMINAL or job.variant_candidate is not None for job in self.jobs.jobs.values())

    def close(self):
        """停止该标签下的后台任务并释放其 DXC 子进程。"""
        self.jobs.close()


class RpcSession:
    """JSON-lines 文档所有者；一个进程可隔离持有多个桌面标签。"""
    # 只读请求不推进 revision；其余已支持操作保守地视为状态写入。
    READ_OPERATIONS = frozenset(('ping', 'snapshot', 'mdf_snapshot', 'mmtr_inspect_structure',
        'material_plan', 'mmtr_validate_interfaces', 'mmtr_material_usage', 'disassemble', 'reconstruct',
        'export_shader', 'export_layout', 'report', 'mdf_report', 'export_compiled'))

    def __init__(self):
        self.contexts = {'default': _RpcContext('default')}
        self.retired = set()

    @property
    def _default(self):
        return self.contexts['default']

    # 兼容现有桌面测试和旧调用方；新请求通过 document_id 访问具体上下文。
    @property
    def service(self):
        return self._default.service

    @service.setter
    def service(self, value):
        self._default.service = value

    @property
    def lock(self):
        return self._default.lock

    @property
    def revision(self):
        return self._default.revision

    @revision.setter
    def revision(self, value):
        self._default.revision = value

    @property
    def dll(self):
        return self._default.dll

    @dll.setter
    def dll(self, value):
        self._default.dll = value

    @property
    def jobs(self):
        return self._default.jobs

    def _context(self, context_id):
        """解析请求上下文；未知标签明确报错，避免把结果写入当前标签。"""
        if not isinstance(context_id, str) or not context_id.strip():
            raise EditError('document_id 必须是非空字符串。')
        context = self.contexts.get(context_id)
        if context is None:
            raise EditError('文档标签不存在，请先创建或重新打开。')
        return context

    def _create_context(self, context_id):
        """创建空文档标签；重复创建保留原上下文，保证重试幂等。"""
        if not isinstance(context_id, str) or not context_id.strip():
            raise EditError('document_id 必须是非空字符串。')
        if context_id in self.retired:
            raise EditError('文档标签已关闭，不能重用其 document_id。')
        if context_id not in self.contexts:
            self.contexts[context_id] = _RpcContext(context_id)
        return self.contexts[context_id]

    def _with_context(self, result, context_id):
        if isinstance(result, dict):
            return {**result, 'document_id': context_id}
        return result

    def _sources(self):
        """聚合所有打开源文件，包含迁移分析依赖；不把会话对象放进候选。"""
        paths = set()
        for context in self.contexts.values():
            service = context.service
            for doc in (service.doc, service.mdf, service.mdf_link_doc):
                if doc and doc.source:
                    paths.add(Path(doc.source).resolve())
            if service.mdf_migration:
                paths.update(Path(service.mdf_migration[key]['source']).resolve() for key in ('source_mmtr', 'target_mmtr'))
        return tuple(paths)

    def _find_source(self, path, exclude=None):
        """按规范路径和硬链接查找源文件所有者，供打开定位与写入保护共用。"""
        path = Path(path).resolve()
        for other_id, context in self.contexts.items():
            if other_id == exclude:
                continue
            for doc in (context.service.doc, context.service.mdf):
                if doc and doc.source:
                    source = Path(doc.source).resolve()
                    if path == source or (path.exists() and source.exists() and path.samefile(source)):
                        return other_id
        return None

    def _check_open(self, context_id, path):
        """同源与硬链接只允许一个可编辑上下文，防止多份 MDF 各自导出。"""
        owner = self._find_source(path, exclude=context_id)
        if owner is not None:
            raise EditError(f'该源文件已在其他标签中打开（{owner}），请切换到已有标签。')

    def handle(self, request):
        op = request['op']
        context_id = request.get('document_id', 'default')
        if op == 'workspace_new':
            context = self._create_context(context_id)
            return {'document_id': context.context_id, 'revision': context.revision}
        if op == 'workspace_list':
            return {'documents': [
                {'document_id': context.context_id,
                 'source': str(context.service.doc.source) if context.service.doc else None,
                 'mdf_source': str(context.service.mdf.source) if context.service.mdf and context.service.mdf.source else None,
                 'dirty': context.unsaved(), 'pending': context.pending(),
                 'revision': context.revision}
                for context in self.contexts.values()
            ]}
        if op == 'workspace_find':
            return {'document_id': self._find_source(request['path'])}
        if op == 'workspace_state':
            # 同锁返回文档、保存状态及待处理任务，避免后台提交夹在多个恢复请求之间。
            from .material_jobs import TERMINAL
            context = self._context(context_id)
            with context.lock, context.jobs.lock:
                return {'document_id': context_id, 'revision': context.revision,
                        'exported_revision': context.exported_revision,
                        'doc': context.service.handle({'op': 'snapshot'}),
                        'mdf': context.service.handle({'op': 'mdf_snapshot'}),
                        'jobs': [context.jobs._status(job) for job in context.jobs.jobs.values()
                                 if job.state not in TERMINAL or job.variant_candidate is not None]}
        if op == 'workspace_close':
            context = self._context(context_id)
            if (context.unsaved() or context.pending()) and request.get('discard') is not True:
                raise EditError('标签有未保存修改或后台任务，请确认放弃后关闭。')
            context.close()
            del self.contexts[context_id]
            self.retired.add(context_id)
            return {'document_id': context_id, 'closed': True}
        context = self._context(context_id)
        # JSON-lines 分发串行；任务导出也在调用线程执行，因此预检覆盖写入时的全部源。
        context.service.workspace_sources = self._sources()
        if op in ('open', 'mdf_open'):
            self._check_open(context_id, request['path'])
        if op in ('export', 'export_shader', 'export_compiled', 'export_layout', 'report', 'mdf_export', 'mdf_report', 'material_project_save'):
            context.service.check_write_target(request['path'])
        if op == 'material_job_status':
            return self._with_context(context.jobs.status(request['job_id']), context_id)
        if op == 'material_job_cancel':
            return self._with_context(context.jobs.cancel(request['job_id']), context_id)
        if op == 'material_job_result':
            return self._with_context(context.jobs.result(request['job_id'], request.get('offset', 0), request.get('limit', 40)), context_id)
        if op == 'material_variant_export':
            return self._with_context(context.jobs.export_variant(request['job_id']), context_id)
        if op == 'material_variant_discard':
            return self._with_context(context.jobs.discard_variant(request['job_id']), context_id)
        with context.lock:
            if 'dll' in request and request['dll'] != context.dll:
                context.dll = request['dll']
                context.revision += 1
            if op == 'material_removal_preview':
                return self._with_context(context.jobs.removal_preview(request.get('actions'), request.get('material_index'),
                                                 request.get('request_key'), variant=request.get('variant')), context_id)
            if op == 'material_removal_submit':
                return self._with_context(context.jobs.removal_submit(request.get('actions'), request.get('material_index'),
                    request.get('expected_plan'), request.get('request_key'), variant=request.get('variant')), context_id)
            if op == 'material_job_preview':
                return self._with_context(context.jobs.preview(request.get('actions'), request.get('record_index'), variant=request.get('variant')), context_id)
            if op == 'material_job_submit':
                return self._with_context(context.jobs.submit(request.get('actions'), request.get('record_index'),
                                        request.get('expected_plan'), request.get('request_key'), variant=request.get('variant')), context_id)
            if op == 'material_project_save':
                from .material_project import save_project
                return self._with_context(save_project(context, request['path'], request.get('actions'), request.get('record_index'),
                                    variant=request.get('variant'), overwrite=request.get('overwrite', False)), context_id)
            if op == 'material_project_read':
                from .material_project import read_project
                return self._with_context(read_project(context, request['path']), context_id)
            if op in ('material_plan', 'material_apply'):
                request = {**request, 'source_revision': context.revision}
            try:
                result = context.service.handle(request)
            finally:
                # 旧同步操作可能先修改再报错，失败也不能让后台候选跨越该编辑提交。
                if op not in self.READ_OPERATIONS:
                    context.revision += 1
            if op in ('open', 'export'):
                context.exported_revision = context.revision
            if isinstance(result, dict):
                result = {**result, 'revision': context.revision, 'exported_revision': context.exported_revision}
            return self._with_context(result, context_id)

    def close(self):
        for context in list(self.contexts.values()):
            context.close()


def main():
    if hasattr(sys.stdin,'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8')
        sys.stdout.reconfigure(encoding='utf-8')
    session = RpcSession()
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                result = {'ok':True,'result':session.handle(request)}
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                result = {'ok':False,'error':str(exc)}
                if isinstance(exc,DxcProcessError):result['details']=exc.details()
            sys.stdout.write(json.dumps(result,ensure_ascii=False)+'\n')
            sys.stdout.flush()
    finally:
        session.close()


if __name__ == '__main__':
    main()
