"""材质输入读取统计测试：合成 DXIL 文本，不依赖 DXC 或游戏资产。"""
import unittest

from mmtr_editor.material_addresses import MaterialTailLayout
from mmtr_editor.material_usage import material_input_usage, shader_input_reads

LAYOUT = MaterialTailLayout(32, ('texA', 'texB'), ('buf',))
MATERIAL = {'name': 'm', 'mmtrs': [], 'properties': [
    {'name': 'p0', 'offset': 0, 'byte_size': 4},
    {'name': 'p1', 'offset': 4, 'byte_size': 4},
    {'name': 'p2', 'offset': 16, 'byte_size': 16}]}


# heap 材质 CB：第0行读 p1；第2行 lane0 为 texA 句柄，lane1 的 texB 只参与算术（仅描述符）。
HEAP_IR = '''define void @main() {
  %heap = call %dx.types.Handle @dx.op.createHandleFromHeap(i32 218, i32 %descriptor, i1 false, i1 false)
  %cb = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %heap, %dx.types.ResourceProperties { i32 13, i32 32768 })
  %row0 = call %dx.types.CBufRet.i32 @dx.op.cbufferLoadLegacy.i32(i32 59, %dx.types.Handle %cb, i32 0)
  %p1 = extractvalue %dx.types.CBufRet.i32 %row0, 1
  %dead = extractvalue %dx.types.CBufRet.i32 %row0, 0
  %use1 = add i32 %p1, 1
  %row2 = call %dx.types.CBufRet.i32 @dx.op.cbufferLoadLegacy.i32(i32 59, %dx.types.Handle %cb, i32 2)
  %texA = extractvalue %dx.types.CBufRet.i32 %row2, 0
  %texB = extractvalue %dx.types.CBufRet.i32 %row2, 1
  %index = add i32 %texA, 0
  %handle = call %dx.types.Handle @dx.op.createHandleFromHeap(i32 218, i32 %index, i1 false, i1 false)
  %report = add i32 %texB, 7
  ret void
}
'''

# 静态绑定的 UserMaterial：第1行 lane0（偏移16，p2）；按名称绑定的 texB 被采样。
STATIC_IR = '''; UserMaterial                      cbuffer      NA          NA     CB2            cb2     1
; texB                              texture     f32          2d      T0             t0     1
define void @main() {
  %cbh = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 2, i32 2, i32 2, i1 false)
  %row1 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %cbh, i32 1)
  %p2 = extractvalue %dx.types.CBufRet.f32 %row1, 0
  %use2 = fadd float %p2, 1.0
  %th = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 0, i32 0, i32 0, i1 false)
  %sample = call %dx.types.ResRet.f32 @dx.op.sample.f32(i32 60, %dx.types.Handle %th, %dx.types.Handle %s, float 0.0, float 0.0)
  ret void
}
'''


class FakeDocument:
    def __init__(self, irs):
        self.irs = irs

    def current_info(self):
        return {'shaders': [{'index': i} for i in range(len(self.irs))]}

    def shader_bytes(self, index):
        return self.irs[index].encode('utf-8')


def usage(irs, material=MATERIAL):
    return material_input_usage(FakeDocument(irs), material, LAYOUT, lambda payload: payload.decode('utf-8'))


def states(report, kind):
    return {row['name']: row['state'] for row in report[kind]}


class MaterialUsageTests(unittest.TestCase):
    def test_heap_reads_split_parameter_handle_and_descriptor(self):
        reads = shader_input_reads(HEAP_IR)
        self.assertEqual(reads['record'], {4, 32, 36})
        self.assertEqual(reads['record_handles'], {32})
        report = usage([HEAP_IR])
        self.assertEqual(states(report, 'parameters'), {'p0': 'unused', 'p1': 'used', 'p2': 'unused'})
        self.assertEqual(states(report, 'textures'), {'texA': 'used', 'texB': 'descriptor'})
        self.assertEqual(states(report, 'gpbf'), {'buf': 'unused'})
        self.assertEqual(report['complete'], {'parameters': True, 'textures': True})

    def test_static_cbuffer_and_named_texture_are_counted(self):
        reads = shader_input_reads(STATIC_IR)
        self.assertEqual(reads['cbuffer'], {16})
        self.assertEqual(reads['textures'], {'texB'})
        report = usage([HEAP_IR, STATIC_IR])
        self.assertEqual(states(report, 'parameters')['p2'], 'used')
        self.assertEqual(states(report, 'textures'), {'texA': 'used', 'texB': 'used'})
        self.assertEqual(next(r for r in report['textures'] if r['name'] == 'texB')['shader_count'], 2)

    def test_identical_payloads_are_analyzed_once(self):
        report = usage([HEAP_IR, HEAP_IR])
        self.assertEqual(report['shaders_analyzed'], 1)
        self.assertEqual(report['shader_count'], 2)
        self.assertEqual(next(r for r in report['parameters'] if r['name'] == 'p1')['shader_count'], 2)

    # 未知材质读取不能被当作未读取：参数与尾部都降为 unknown，并列出失败的 Shader。
    def test_unknown_record_read_downgrades_unused_to_unknown(self):
        unknown = HEAP_IR.replace('i32 59, %dx.types.Handle %cb, i32 0)', 'i32 59, %dx.types.Handle %cb, i32 %row)')
        report = usage([unknown])
        self.assertEqual(report['complete'], {'parameters': False, 'textures': False})
        self.assertEqual(states(report, 'parameters')['p0'], 'unknown')
        self.assertEqual(states(report, 'gpbf'), {'buf': 'unknown'})
        self.assertEqual(report['failures'][0]['scope'], 'record')

    def test_dynamic_static_row_only_affects_parameters(self):
        dynamic = STATIC_IR.replace('%dx.types.Handle %cbh, i32 1)', '%dx.types.Handle %cbh, i32 %row)')
        report = usage([HEAP_IR, dynamic])
        self.assertEqual(report['complete'], {'parameters': False, 'textures': True})
        self.assertEqual(states(report, 'parameters')['p0'], 'unknown')
        self.assertEqual(states(report, 'gpbf'), {'buf': 'unused'})

    def test_unsupported_static_aggregate_consumer_is_reported(self):
        escaped = STATIC_IR.replace('  ret void', '  call void @escape(%dx.types.CBufRet.f32 %row1)\n  ret void')
        report = usage([escaped])
        self.assertFalse(report['complete']['parameters'])
        self.assertIn('aggregate', report['failures'][0]['error'])

    def test_unrecognized_static_load_and_handle_escape_are_unknown(self):
        direct = STATIC_IR.replace('cbufferLoadLegacy.f32(i32 59', 'cbufferLoad.f32(i32 58')
        escaped = STATIC_IR.replace('  ret void', '  call void @escape(%dx.types.Handle %cbh)\n  ret void')
        for ir in (direct, escaped):
            with self.subTest(ir=ir):
                report = usage([ir])
                self.assertFalse(report['complete']['parameters'])
                self.assertEqual(states(report, 'parameters')['p0'], 'unknown')

    def test_resource_provenance_and_empty_module_are_unknown(self):
        opaque = STATIC_IR.replace('%dx.types.Handle %cbh, i32 1)', '%dx.types.Handle %opaque, i32 1)')
        for ir in (opaque, '; no function body'):
            report = usage([ir])
            self.assertEqual(report['complete'], {'parameters': False, 'textures': False})

    def test_ssa_names_are_local_to_each_function(self):
        other = '''; Other cbuffer NA NA CB3 cb3 1
define void @helper() {
  %cbh = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 2, i32 3, i32 3, i1 false)
  ret void
}
'''
        report = usage([STATIC_IR+'\n'+other])
        self.assertTrue(report['complete']['parameters'])
        self.assertEqual(states(report, 'parameters')['p2'], 'used')

    def test_descriptor_traversal_limit_and_external_call_are_unknown(self):
        chain = '\n'.join(f'  %c{i} = add i32 '+('%texB' if i == 0 else f'%c{i-1}')+', 1' for i in range(70))
        deep = HEAP_IR.replace('  %report = add i32 %texB, 7', chain)
        escaped = HEAP_IR.replace('  %report = add i32 %texB, 7', '  call void @escape(i32 %texB)')
        for ir in (deep, escaped):
            report = usage([ir])
            self.assertFalse(report['complete']['textures'])
            self.assertEqual(states(report, 'textures')['texB'], 'unknown')

    def test_heap_material_handle_escape_is_unknown(self):
        escaped = HEAP_IR.replace('  ret void', '  call void @escape(%dx.types.Handle %cb)\n  ret void')
        report = usage([escaped])
        self.assertFalse(report['complete']['parameters'])
        self.assertTrue(any(row['scope'] in ('record', 'cbuffer') for row in report['failures']))

    # 参数实例流的读取计入参数；超出参数区的常量地址不解释为尾部字段。
    def test_instance_reads_count_as_parameters_only(self):
        def instance(address):
            return ('; UserMaterialInstances texture byte r/o T0 t0 1\ndefine void @main() {\n'
                    '  %raw = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 0, i32 0, i32 0, i1 false)\n'
                    f'  %load = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %raw, i32 {address}, i32 undef, i8 15, i32 4)\n'
                    '  %v = extractvalue %dx.types.ResRet.i32 %load, 0\n  %u = add i32 %v, 1\n  ret void\n}\n')
        report = usage([instance(16)])
        self.assertEqual(states(report, 'parameters')['p2'], 'used')
        self.assertTrue(report['complete']['textures'])
        report = usage([instance(32)])
        self.assertEqual(states(report, 'textures'), {'texA': 'unknown', 'texB': 'unknown'})
        self.assertIn('实例编号', report['failures'][0]['error'])

    # MMTRS 改变尾部字段位置；在支持重定向前，纹理结果必须标为不完整。
    def test_mmtrs_material_marks_tail_incomplete(self):
        report = usage([HEAP_IR], {**MATERIAL, 'mmtrs': [[1, 0]]})
        self.assertFalse(report['complete']['textures'])
        self.assertEqual(states(report, 'textures'), {'texA': 'unknown', 'texB': 'unknown'})
        self.assertTrue(report['notes'])

    def test_disassembly_failure_marks_everything_incomplete(self):
        def broken(payload):
            raise RuntimeError('bad container')
        report = material_input_usage(FakeDocument([HEAP_IR]), MATERIAL, LAYOUT, broken)
        self.assertEqual(report['complete'], {'parameters': False, 'textures': False})
        self.assertEqual(report['failures'][0]['scope'], 'all')


if __name__ == '__main__':
    unittest.main()
