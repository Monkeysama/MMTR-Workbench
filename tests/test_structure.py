"""独立合成 Program/状态/共享绑定；不依赖游戏资产或 DXC。"""
from dataclasses import FrozenInstanceError
from pathlib import Path
import struct
import unittest

from inspect_mmtr import inspect_data
from mmtr_editor.hashes import murmur3_x86_32, xxh64
from mmtr_editor.model import Document
from mmtr_editor.rpc import Service
from mmtr_editor.structure import parse_structure, document_structure

PATH = Path('MaterialShader/Variation/synthetic.mmtr.240906211')


# 各组共享同一 PS/绑定/状态，测试共享者闭包及非零未知字节的保留。
# record_size 0x108 生成 RE4 记录：0xFF 之前的字段与荒野相同，没有荒野 0x100..0x107 的 8 字节。
def fixture(group_count=4, programs_per_group=1, record_size=0x110, runtime_cache_keys=True):
    record_count = group_count * programs_per_group
    data = bytearray(16+record_count*record_size)
    data[:4] = b'SDF\0'
    struct.pack_into('<HH', data, 4, group_count, programs_per_group)

    def append(blob):
        position = len(data)
        data.extend(blob)
        return position

    names = [append(b'Pass\0' if programs_per_group == 1 else f'Pass{i}\0'.encode())
             for i in range(programs_per_group)]
    cbname = append(b'UserMaterial\0')
    membername = append(b'Value\0')
    texname = append(b'BaseMap\0')
    hash_name = lambda text: murmur3_x86_32(text.encode(), 0xffffffff)
    members = append(struct.pack('<QIHH', membername, hash_name('Value'), 0, 4))
    metadata_cb = append(struct.pack('<QIIIIQ', cbname, hash_name('UserMaterial'), 0, 16, 1, members))
    metadata_srv = append(struct.pack('<QII', texname, hash_name('BaseMap'), 0))
    cb = append(bytes([0, 0, 0, 0, 0, 0, 16, 255]))
    srv = append(bytes([0, 0, 0, 0, 3, 0, 16, 2]))
    layout = append(struct.pack('<II4BI', 1, 0, 0, 2, 0, 0, 12))
    if runtime_cache_keys:
        append(b'layout!!')
    raster = append(struct.pack('<Iiff', 0x1C1, -2, 0.0, 1.0))
    if runtime_cache_keys:
        append(b'raster!!')
    blend = append(struct.pack('<9I', *([0]*8+[3])))
    if runtime_cache_keys:
        append(b'blend!!!')
    depth = append(struct.pack('<Q', 0xFF010081))
    if runtime_cache_keys:
        append(b'depth!!!')
    start = len(data)
    # 足够满足容器结构检查的合成 DXIL，禁止将其当可执行 bitcode 或游戏有效载荷。
    payload = struct.pack('<6I', 0x60, 7, 0x4C495844, 0x100, 16, 4)+b'BC\xc0\xde'
    blob = b'DXBC'+bytes(16)+struct.pack('<III', 1, 72, 1)+struct.pack('<I', 36)+b'DXIL'+struct.pack('<I', 28)+payload
    assert len(blob) == 72
    data.extend(blob + (struct.pack('<Q', xxh64(blob, 0xffffffff)) if runtime_cache_keys else b''))
    struct.pack_into('<Q', data, 8, start)
    for i in range(record_count):
        base = 16+i*record_size
        for field, pointer in [(0, names[i % programs_per_group]), (0x30, start), (0x40, layout), (0x48, raster), (0x50, blend),
                               (0x58, depth), (0x68, cb), (0x70, metadata_cb), (0x88, srv), (0x90, metadata_srv)]:
            struct.pack_into('<Q', data, base+field, pointer)
        struct.pack_into('<I', data, base+0xCC, len(blob))
        struct.pack_into('<I', data, base+0xD4, 2)
        data[base+0xDC] = data[base+0xE8] = 1
        data[base+0xF6] = data[base+0xFC] = 1
        if record_size == 0x110:
            data[base+0x100] = 99
    return bytes(data)


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.model = parse_structure(self.data, PATH)

    def test_readonly_roundtrip_and_all_shader_users(self):
        self.assertEqual(self.model.diagnostics, ())
        self.assertEqual(self.model.source_bytes, self.data)
        self.assertEqual(len(self.model.programs), 4)
        self.assertEqual(len(self.model.shader_users[0]), 4)
        self.assertEqual(self.model.programs[3].target, 3)
        self.assertEqual(self.model.programs[0].tables[2].bindings[0].slots[4], 3)
        self.assertEqual(self.model.programs[0].raw[0x100], 99)
        self.assertTrue(all(len(o.users) == 4 for o in self.model.objects))
        with self.assertRaises(FrozenInstanceError):
            self.model.sha256 = 'changed'
        doc = Document(self.data, PATH)
        self.assertEqual(document_structure(doc), self.model)
        self.assertEqual(doc.build()[0], self.data)
        self.assertEqual(doc.info, inspect_data(self.data, PATH)[1])

    def test_mask_and_count_corruption_reported_without_breaking_legacy_open(self):
        data = bytearray(self.data)
        pointer = struct.unpack_from('<Q', data, 16+0x88)[0]
        data[pointer+6] = 0x80
        doc = Document(data, PATH)
        report = document_structure(doc)
        codes = {d.code for d in report.diagnostics}
        self.assertIn('binding_mask', codes)
        self.assertIn('binding_stage_counts', codes)
        self.assertEqual(report.report()['preflight']['structure'], 'failed')
        self.assertEqual(doc.build()[0], bytes(data))

    def test_range_and_unknown_type_diagnostics_have_source_positions(self):
        data = bytearray(self.data)
        struct.pack_into('<Q', data, 16+0x88, len(data)+100)
        report = parse_structure(data, PATH)
        diag = next(d for d in report.diagnostics if d.code == 'object_range')
        self.assertEqual((diag.program, diag.field_offset), (0, 16+0x88))
        data = bytearray(self.data)
        ptr = struct.unpack_from('<Q', data, 16+0x88)[0]
        data[ptr+7] = 123
        report = parse_structure(data, PATH)
        self.assertIn('binding_type_unknown', [d.code for d in report.diagnostics])
        self.assertEqual(report.programs[0].tables[2].bindings[0].type_code, 123)

    def test_shared_slices_and_same_name_objects_are_not_merged(self):
        data = bytearray(self.data)
        ptr = struct.unpack_from('<Q', data, 16+0x88)[0]
        struct.pack_into('<Q', data, 16+0x110+0x88, ptr+1)
        report = parse_structure(data, PATH)
        tables = [o for o in report.objects if o.kind == 'srvs_bindings']
        self.assertEqual(len(tables), 2)
        self.assertNotEqual(tables[0].identity, tables[1].identity)
        self.assertIn('object_overlap', [d.code for d in report.diagnostics])

    def test_large_input_layout_count_is_bounded(self):
        data = bytearray(self.data)
        ptr = struct.unpack_from('<Q', data, 16+0x40)[0]
        struct.pack_into('<I', data, ptr, 0xffffffff)
        report = parse_structure(data, PATH)
        self.assertIn('object_range', [d.code for d in report.diagnostics])
        self.assertFalse(any(o.kind == 'input_layout' for o in report.objects))

    def test_version_preflight_does_not_claim_shader_or_edit_support(self):
        report = parse_structure(self.data, PATH.with_suffix('.999')).report()
        self.assertIn('resource_version_unverified', [d['code'] for d in report['diagnostics']['items']])
        self.assertEqual(report['preflight']['auto_extension'], 'unproven')
        self.assertEqual(report['preflight']['shader_analysis'], 'not_run')
        self.assertEqual(report['preflight']['state_editing'], 'unsupported')

    def test_total_count_and_inactive_table_counts_are_diagnosed(self):
        data = bytearray(self.data)
        struct.pack_into('<I', data, 16+0xD4, 4)
        data[16+0xDF] = 1  # sampler 无条目但声称 hull 阶段有一个。
        report = parse_structure(data, PATH)
        codes = {d.code for d in report.diagnostics}
        self.assertIn('binding_total', codes)
        self.assertIn('binding_stage_counts', codes)

    def test_snapshot_identity_and_opaque_state_keys(self):
        _, info = inspect_data(self.data, PATH)
        data = bytearray(self.data)
        data[16+0x100] = 100
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            parse_structure(data, PATH, info)
        # 状态 trailer 不按 Shader hash 算法解释，必须作为未知身份原样保留。
        keys = [o for o in self.model.objects if o.kind.endswith('_cache_key')]
        self.assertTrue(keys)
        self.assertTrue(all(dict(o.values)['algorithm'] == 'opaque' for o in keys))
        regions = self.model.report('unmodeled_ranges')
        self.assertGreater(regions['total'], 0)
        coverage = bytearray(self.model.header.shader_start-self.model.header.record_end)
        for start, size in self.model.unmodeled_ranges:
            self.assertTrue(all(v == 0 for v in coverage[start-self.model.header.record_end:start-self.model.header.record_end+size]))
            coverage[start-self.model.header.record_end:start-self.model.header.record_end+size] = bytes([1])*size
        for obj in self.model.objects:
            self.assertEqual(obj.raw, self.data[obj.offset:obj.offset+obj.size])
            start = obj.offset-self.model.header.record_end
            coverage[start:start+obj.size] = bytes([1])*obj.size
        self.assertTrue(all(coverage))

    def test_zero_pointer_and_unknown_optional_table_are_explicit(self):
        data = bytearray(self.data)
        data[16+0xF8] = 1
        report = parse_structure(data, PATH)
        self.assertIn('compact_pointer', [d.code for d in report.diagnostics])
        data = bytearray(self.data)
        bindings, metadata = struct.unpack_from('<QQ', data, 16+0x88)
        struct.pack_into('<QQ', data, 16+0xA8, bindings, metadata)
        data[16+0xFE] = 1
        data[16+0xF4] = 1
        struct.pack_into('<I', data, 16+0xD4, 3)
        report = parse_structure(data, PATH)
        self.assertEqual(len(report.programs[0].tables[4].bindings), 1)
        self.assertIn('optional_resource_unverified', [d.code for d in report.diagnostics])

    def test_physical_objects_are_paged_and_raw_preview_bounded(self):
        objects = self.model.report('objects', limit=1)
        self.assertEqual(len(objects['items']), 1)
        obj = objects['items'][0]
        self.assertLessEqual(len(obj['raw_preview_hex']), 512)
        self.assertEqual(obj['users']['total'], 4)
        self.assertEqual(len(obj['users']['items']), 1)
        remaining = self.model.report('object_users', index=obj['index'], offset=3, limit=1)
        self.assertEqual(remaining['items'][0]['program'], 3)
        metadata = self.model.report('metadata', index=0, limit=1)
        self.assertEqual(metadata['members']['items'][0]['byte_offset'], 0)

    def test_rpc_current_candidate_and_pagination(self):
        service = Service()
        service.doc = Document(self.data, PATH)
        first = service.handle({'op': 'mmtr_inspect_structure', 'view': 'shader_users', 'index': 0, 'limit': 1})
        self.assertEqual((first['total'], first['next_offset']), (4, 1))
        second = service.handle({'op': 'mmtr_inspect_structure', 'view': 'shader_users', 'index': 0, 'offset': 1, 'limit': 1})
        self.assertEqual(second['items'][0]['program'], 1)
        before = document_structure(service.doc)
        service.doc.rename(service.doc.info['records'][0]['name_offset'], 'Next')
        after = document_structure(service.doc)
        self.assertIsNot(before, after)
        self.assertEqual(after.programs[0].name, 'Next')
        self.assertEqual(service.doc.original, self.data)
        with self.assertRaises(ValueError):
            after.report('program', 10)
        with self.assertRaises(ValueError):
            after.report(limit=0)

    def test_resized_shader_snapshot_uses_candidate_pointers_and_lengths(self):
        doc = Document(self.data, PATH)
        original = document_structure(doc)
        blob = bytearray(doc.shader_bytes(0))
        # 修改合成容器长度，不运行伪 bitcode；验证新结构不会错误复用旧 info 的引用尺寸。
        struct.pack_into('<I', blob, 24, len(blob)+4)
        struct.pack_into('<I', blob, 40, 32)
        struct.pack_into('<I', blob, 48, 8)
        struct.pack_into('<I', blob, 64, 8)
        blob.extend(bytes(4))
        doc.replace_shader(0, bytes(blob))
        candidate = document_structure(doc)
        self.assertNotEqual(original.sha256, candidate.sha256)
        self.assertTrue(all(p.shaders[0].size == 76 for p in candidate.programs))
        self.assertEqual(candidate.source_bytes, doc.build()[0])
        self.assertEqual(doc.original, self.data)


if __name__ == '__main__':
    unittest.main()
