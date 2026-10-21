"""固定输入的 CPU 回归基线；串行运行隔离 DXC，绝不安装或覆盖源资产。

manifest schema 1: samples=[{id,mmtr:{path,sha256},mdf?:{path,sha256},
material_index?,record_index?,scenarios?:[parameter|texture|texture_parameter|parameter_texture]}],
dxc?:{path,sha256}。相对路径以 manifest 所在目录为基准。
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mmtr_editor.model import Document
from mmtr_editor.mdf import MdfDocument, _public_data, master_matches, user_material_layout
from mmtr_editor.material_extension import current_layout
from mmtr_editor.rpc import Service


# 摘要只包含确定内容；耗时、机器与绝对路径不进入语义比较。
def digest(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode())


# 身份必须先于解析核对；缺失可报告 skip，内容改变必须失败，禁止自动替换样本。
def verified_input(base, entry):
    path = (base / entry['path']).resolve()
    data = path.read_bytes()
    if digest(data) != entry['sha256']:
        raise ValueError(f'输入 SHA256 不匹配: {path}')
    return path, data


def summary(doc):
    info = doc.current_info()
    return {'records': [{'index': r['index'], 'name': r['name'],
                         'refs': [(s['field'], s['shader_index']) for s in r['shader_references']],
                         'tables': r['metadata_tables']} for r in info['records']],
            'metadata': info['metadata'], 'integrity': doc.integrity,
            'shaders': [digest(doc.shader_bytes(s['index'])) for s in info['shaders']]}


# 比较 MDF 语义而非重排后的指针；既有参数值/槽路径与所有非关联材质必须保持。
def assert_material_preserved(before, after, linked):
    if len(before.materials) != len(after.materials):
        raise AssertionError('材质数量改变')
    for old, new in zip(before.materials, after.materials):
        old, new = _public_data(old), _public_data(new)
        if old['index'] not in linked:
            if old != new:
                raise AssertionError(f'非关联材质改变: {old["name"]}')
            continue
        for key in ('properties', 'textures', 'gpbf'):
            if new[key][:len(old[key])] != old[key]:
                raise AssertionError(f'原 {key} 改变: {old["name"]}')
        for key in old.keys() - {'properties', 'textures', 'property_block_size', 'property_count', 'texture_count'}:
            if old[key] != new[key]:
                raise AssertionError(f'材质字段改变: {old["name"]}.{key}')


# 每次从同一字节快照新建 Service；复用实际 RPC 核心及隔离子进程，非 MCP 传输计时。
def capture_case(sample, mmtr, mdf, dll, folder, scenario):
    started = time.perf_counter()
    path, data = mmtr
    times = {}

    def timed(label, operation):
        start = time.perf_counter()
        result = operation()
        times[label] = time.perf_counter() - start
        return result

    service = Service()
    service.doc = timed('parse_mmtr', lambda: Document(data, path))
    original = summary(service.doc)
    source_shaders = original['shaders']
    layouts = {}
    reports = []
    if mdf:
        service.mdf = timed('parse_mdf', lambda: MdfDocument(mdf[1], mdf[0]))
        service.mdf_selected = sample['material_index']
        linked = [m['index'] for m in service.mdf.materials if master_matches(m['master'], str(path))]
        if service.mdf_selected not in linked:
            raise ValueError('所选材质与固定 MMTR 不关联')
        layouts['before'] = asdict(current_layout(service.doc, service.mdf.material(service.mdf_selected)))
    else:
        linked = []
    actions = {'noop': (), 'parameter': ('parameter',), 'texture': ('texture',),
               'texture_parameter': ('texture', 'parameter'),
               'parameter_texture': ('parameter', 'texture')}[scenario]
    if actions and (not mdf or not dll):
        raise ValueError('扩展基线必须提供固定 MDF 与 DXC')
    for action in actions:
        name = 'WorkbenchBaselineParameter' if action == 'parameter' else 'WorkbenchBaselineTexture'
        req = {'name': name, 'auto_sync': True, 'dll': str(dll)}
        if action == 'parameter':
            req.update(op='mmtr_add_cbuffer_member', cbuffer='UserMaterial', byte_size=16,
                       byte_offset=user_material_layout(service.doc.current_info())['buffer_size'])
        else:
            req.update(op='mmtr_add_resource', kind='srvs', record_index=sample['record_index'])
        result = timed(action, lambda: service.handle(req))
        report = result['interface_sync']
        if not report['validation']['valid']:
            raise AssertionError('接口验证失败')
        reports.append(report)
        for index in linked:
            req = ({'op': 'mdf_add_property', 'values': [0.0] * 4} if action == 'parameter' else
                   {'op': 'mdf_add_texture', 'path': 'systems/rendering/NullBlack.tex'})
            service.handle({**req, 'material_index': index, 'name': name})
    built, _ = timed('build_mmtr', service.doc.build)
    reopened = timed('reparse_mmtr', lambda: Document(built, path))
    if any(reopened.integrity[k] for k in ('name_hash_mismatches', 'shader_trailer_mismatches')):
        raise AssertionError('重开完整性错误')
    if not actions and built != data:
        raise AssertionError('原样重建改变字节')
    changed = {s['index'] for report in reports for s in report['shader_reports']}
    candidate_summary = summary(reopened)
    hashes = candidate_summary['shaders']
    if len(hashes) != len(source_shaders) or any(
            a != b for i, (a, b) in enumerate(zip(source_shaders, hashes)) if i not in changed):
        raise AssertionError('非目标 Shader 载荷改变')
    if actions:
        old_cb = user_material_layout(service.doc.info)
        new_cb = user_material_layout(reopened.info)
        if new_cb['members'][:len(old_cb['members'])] != old_cb['members']:
            # 重建允许物理位置移动；只比较成员的语义字段。
            fields = lambda cb: [(m['name'], m['byte_offset'], m['byte_size']) for m in cb['members']]
            if fields(new_cb)[:len(old_cb['members'])] != fields(old_cb):
                raise AssertionError('旧 UserMaterial 成员布局改变')
    semantic = {'mmtr': candidate_summary}
    if mdf:
        rebuilt, _ = timed('build_mdf', service.mdf.build)
        new_mdf = MdfDocument(rebuilt, mdf[0])
        assert_material_preserved(MdfDocument(mdf[1], mdf[0]), new_mdf, linked)
        layouts['after'] = asdict(current_layout(reopened, new_mdf.material(service.mdf_selected)))
        if layouts['after']['parameter_bytes'] != layouts['before']['parameter_bytes'] + 16 * actions.count('parameter'):
            raise AssertionError('参数区尺寸不符合追加预期')
        for index in linked:
            material = new_mdf.material(index)
            if 'parameter' in actions:
                added = [p for p in material['properties'] if p['name'] == 'WorkbenchBaselineParameter']
                if len(added) != 1 or added[0]['values'] != [0.0] * 4:
                    raise AssertionError('新增参数默认值不符合预期')
            if 'texture' in actions:
                added = [t for t in material['textures'] if t['name'] == 'WorkbenchBaselineTexture']
                if len(added) != 1 or added[0]['path'] != 'systems/rendering/NullBlack.tex':
                    raise AssertionError('新增槽位路径不符合预期')
        semantic['mdf'] = [_public_data(m) for m in new_mdf.materials]
        if not actions and rebuilt != mdf[1]:
            raise AssertionError('原样 MDF 重建改变字节')
        (folder / mdf[0].name).write_bytes(rebuilt)
    timed('write_mmtr', lambda: (folder / path.name).write_bytes(built))
    return {'semantic': semantic, 'fingerprint': fingerprint(semantic), 'seconds': times,
            'case_seconds': time.perf_counter() - started,
            'layouts': layouts, 'extension_reports': reports,
            'unchanged_shaders': sum(a == b for a, b in zip(source_shaders, hashes)),
            'output_sha256': digest(built), 'passed': True}


# 新建证据目录防止覆盖既有输出；全部输入先核对，finally 再核对源文件身份。
def capture(manifest_path, output, repeat):
    if repeat < 1:
        raise ValueError('repeat 必须大于零')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema') != 1:
        raise ValueError('不支持的 manifest schema')
    ids = [s['id'] for s in manifest['samples']]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r'[A-Za-z0-9_-]+', name) for name in ids):
        raise ValueError('样本 id 必须唯一，且只包含字母、数字、下划线或连字符')
    inputs = {}
    missing = []
    for sample in manifest['samples']:
        try:
            inputs[sample['id']] = {kind: verified_input(manifest_path.parent, sample[kind])
                                    for kind in ('mmtr', 'mdf') if kind in sample}
        except FileNotFoundError as exc:
            if sample.get('optional', False):
                missing.append({'sample': sample['id'], 'reason': str(exc)})
            else:
                raise
    dxc = verified_input(manifest_path.parent, manifest['dxc']) if manifest.get('dxc') else None
    protected = [item for files in inputs.values() for item in files.values()] + ([dxc] if dxc else [])
    output = output.resolve()
    if any(output == p or output in p.parents for p, _ in protected):
        raise ValueError('输出目录包含受保护的输入')
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema': 1, 'manifest_sha256': digest(manifest_path.read_bytes()),
              'harness_sha256': digest(Path(__file__).read_bytes()),
              'core_sha256': {str(p.relative_to(ROOT)).replace('\\', '/'): digest(p.read_bytes())
                              for p in [ROOT / 'inspect_mmtr.py', *sorted((ROOT / 'mmtr_editor').glob('*.py'))]},
              'mode': 'source RPC core, serial isolated DXC; not MCP transport',
              'python': sys.version, 'machine': platform.platform(), 'repeat': repeat,
              'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'skipped': missing, 'cases': [], 'passed': False, 'game_tested': False}
    try:
        for sample in manifest['samples']:
            if sample['id'] not in inputs:
                continue
            files = inputs[sample['id']]
            for scenario in ['noop'] + sample.get('scenarios', []):
                runs = []
                for iteration in range(repeat):
                    report['current_case'] = {'sample': sample['id'], 'scenario': scenario, 'iteration': iteration}
                    folder = output / sample['id'] / scenario / str(iteration)
                    folder.mkdir(parents=True)
                    row = capture_case(sample, files['mmtr'], files.get('mdf'), dxc[0] if dxc else None,
                                       folder, scenario)
                    (folder / 'result.json').write_text(json.dumps(row, indent=2), encoding='utf-8')
                    runs.append(row)
                    print(sample['id'], scenario, iteration, 'PASS', flush=True)
                if len({r['fingerprint'] for r in runs}) != 1:
                    raise AssertionError('重复运行的语义摘要不一致')
                durations = [r['case_seconds'] for r in runs]
                report['cases'].append({'sample': sample['id'], 'scenario': scenario,
                    'fingerprint': runs[0]['fingerprint'], 'seconds': durations,
                    'median_seconds': statistics.median(durations),
                    'range_seconds': [min(durations), max(durations)]})
        report['passed'] = bool(report['cases'])
        report.pop('current_case', None)
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        report['sources_preserved'] = all(p.read_bytes() == data for p, data in protected)
        report['passed'] = report['passed'] and report['sources_preserved']
        (output / 'baseline.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


# 比较要求同一个固定 manifest、用例集合与完整通过状态；计时变化单独报告，不混入语义。
def compare(previous, current):
    if previous['manifest_sha256'] != current['manifest_sha256']:
        raise ValueError('manifest 身份变化，不能比较')
    if any(not r['passed'] or not r['sources_preserved'] or r['skipped'] for r in (previous, current)):
        raise ValueError('不完整或失败的运行不能充当回归基线')
    old = {(c['sample'], c['scenario']): c for c in previous['cases']}
    new = {(c['sample'], c['scenario']): c for c in current['cases']}
    if old.keys() != new.keys():
        raise ValueError('基线用例集合不同')
    return [{'sample': key[0], 'scenario': key[1],
             'semantic_equal': old[key]['fingerprint'] == new[key]['fingerprint'],
             'previous_seconds': old[key]['median_seconds'], 'current_seconds': new[key]['median_seconds']}
            for key in old]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args()
    result = capture(args.manifest.resolve(), args.output, args.repeat)
    if args.compare:
        comparisons = compare(json.loads(args.compare.read_text(encoding='utf-8')), result)
        (args.output / 'comparison.json').write_text(json.dumps(comparisons, indent=2), encoding='utf-8')
        if not all(row['semantic_equal'] for row in comparisons):
            return 1
    return 0 if result['passed'] and not result['skipped'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
