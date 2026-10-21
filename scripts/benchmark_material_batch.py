"""固定语料的顺序/批量追加对照；候选只写全新目录，默认交错重复三轮。

顺序组使用相同核心，每次提交一个动作，便于比较真实 DXC 次数。不是旧版 EXE/MCP 传输计时。
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.capture_mmtr_baseline import assert_material_preserved, verified_input, digest
from mmtr_editor.material_extension import current_layout
from mmtr_editor.mdf import MdfDocument, _public_data
from mmtr_editor.model import Document
from mmtr_editor.rpc import Service


# 按字段/槽身份比较，不要求两种前缀分配方式的文件 hash 一致。
def semantics(document, mdf):
    info = document.info
    by_kind = {kind: {e['offset']: e['name'] for e in rows} for kind, rows in info['metadata'].items()}
    return {'materials': [_public_data(m) for m in mdf.materials],
            'buffers': [(e['name'], e['buffer_size'], [(m['name'], m['byte_offset'], m['byte_size']) for m in e['members']])
                        for e in info['metadata']['cbuffers']],
            'programs': [{'refs': [(r['field'], r['shader_index'], r['stage']) for r in p['shader_references']],
                          'tables': {k: [by_kind[k][o] for o in offsets] for k, offsets in p['metadata_tables'].items()},
                          'binding_counts': p['per_stage_counts_raw']} for p in info['records']]}


def run_case(sample, mmtr, mdf, dll, actions, output, mode):
    source = Document(mmtr[1], mmtr[0])
    original_mdf = MdfDocument(mdf[1], mdf[0])
    service = Service()
    service.doc, service.mdf, service.mdf_selected = source, original_mdf, sample['material_index']
    calls, counters = [], Counter()
    started = time.perf_counter()
    for group in ([actions] if mode == 'batch' else [[action] for action in actions]):
        tick = time.perf_counter()
        report = service.handle({'op': 'material_apply', 'dll': str(dll), 'actions': group,
                                 'record_index': sample['record_index']})
        calls.append({'seconds': time.perf_counter()-tick, 'result': report})
        counters.update(report['interface_sync']['statistics'])
        print(mode, 'step', len(calls), 'PASS', round(calls[-1]['seconds'], 3), flush=True)
    edit_seconds = time.perf_counter()-started
    data, mdf_bytes = service.doc.build()[0], service.mdf.build()[0]
    final_doc, final_mdf = Document(data, mmtr[0]), MdfDocument(mdf_bytes, mdf[0])
    if final_doc.integrity['name_hash_mismatches'] or final_doc.integrity['shader_trailer_mismatches']:
        raise AssertionError('最终候选完整性失败')
    linked = calls[-1]['result']['target_materials']
    assert_material_preserved(MdfDocument(mdf[1], mdf[0]), final_mdf, linked)
    expected = calls[-1]['result']['plan']['new']
    for index in linked:
        layout = current_layout(final_doc, final_mdf.material(index))
        assert layout.parameter_bytes == expected['parameter_bytes']
        assert layout.texture_names == tuple(expected['texture_names'])
        assert layout.gpbf_names == tuple(expected['gpbf_names'])
    changed = {row['index'] for call in calls for row in call['result']['interface_sync']['shader_reports']}
    original_doc = Document(mmtr[1], mmtr[0])
    assert all(final_doc.shader_bytes(i) == original_doc.shader_bytes(i)
               for i in range(len(final_doc.info['shaders'])) if i not in changed)
    output.mkdir(parents=True)
    (output/mmtr[0].name).write_bytes(data)
    (output/mdf[0].name).write_bytes(mdf_bytes)
    result = {'passed': True, 'mode': mode, 'edit_seconds': edit_seconds, 'statistics': dict(counters),
              'calls': calls, 'mmtr_sha256': digest(data), 'mdf_sha256': digest(mdf_bytes),
              'semantic_sha256': digest(json.dumps(semantics(final_doc, final_mdf), sort_keys=True).encode())}
    (output/'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


# 每轮交换先后顺序，保留单次结果，避免固定分组顺序的热状态变化被误认作算法差异。
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--sample', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    if args.repeat < 1:
        raise ValueError('repeat 必须大于零')
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    sample = next(s for s in manifest['samples'] if s['id'] == args.sample)
    mmtr = verified_input(args.manifest.parent, sample['mmtr'])
    mdf = verified_input(args.manifest.parent, sample['mdf'])
    dll = verified_input(args.manifest.parent, manifest['dxc'])
    output = args.output.resolve()
    if any(output == p or output in p.parents for p, _ in (mmtr, mdf, dll)):
        raise ValueError('输出目录包含源文件')
    output.mkdir(parents=True, exist_ok=False)
    actions = [{'action': 'parameter', 'name': 'BatchBenchParam4', 'byte_size': 4},
               {'action': 'texture', 'name': 'BatchBenchTextureA'},
               {'action': 'parameter', 'name': 'BatchBenchParam12', 'byte_size': 12},
               {'action': 'texture', 'name': 'BatchBenchTextureB'}]
    report = {'mode': 'source core; serial isolated DXC; one-action transactions vs mixed batch',
              'python': sys.version, 'machine': platform.platform(),
              'core_sha256': {p.name: digest(p.read_bytes()) for p in sorted((ROOT/'mmtr_editor').glob('*.py'))},
              'sample': sample['id'], 'input_sha256': [digest(data) for _, data in (mmtr, mdf, dll)],
              'runs': [], 'passed': False}
    try:
        for iteration in range(args.repeat):
            rows = []
            for mode in ('sequential', 'batch') if iteration % 2 == 0 else ('batch', 'sequential'):
                row = run_case(sample, mmtr, mdf, dll[0], actions, output/str(iteration)/mode, mode)
                rows.append(row)
                report['runs'].append({'iteration': iteration, 'mode': mode, 'seconds': row['edit_seconds'],
                                       'statistics': row['statistics'], 'semantic_sha256': row['semantic_sha256']})
            assert rows[0]['semantic_sha256'] == rows[1]['semantic_sha256']
        assert len({r['semantic_sha256'] for r in report['runs']}) == 1
        report['timing'] = {}
        for mode in ('batch', 'sequential'):
            values = [r['seconds'] for r in report['runs'] if r['mode'] == mode]
            report['timing'][mode] = {'median': statistics.median(values), 'range': [min(values), max(values)]}
        report['passed'] = True
    finally:
        report['sources_preserved'] = all(p.read_bytes() == data for p, data in (mmtr, mdf, dll))
        report['code_preserved'] = all(digest((ROOT/'mmtr_editor'/name).read_bytes()) == sha
                                       for name, sha in report['core_sha256'].items())
        report['passed'] = report['passed'] and report['sources_preserved'] and report['code_preserved']
        (output/'benchmark.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
