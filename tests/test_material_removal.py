"""P5 删除覆盖和最终布局测试：未使用字段可进入候选，未知/使用字段必须阻止。"""
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from mmtr_editor.material_addresses import MaterialTailLayout
from mmtr_editor.material_removal import (
    analyze_material_removal, name_numeric_locals, plan_removal_layout, prove_shader_removal, remap_shader_reads,
)
from mmtr_editor.model import EditError


IR = '''%dx.types.Handle = type { i8* }
define void @main() {
  %heap = call %dx.types.Handle @dx.op.createHandleFromHeap(i32 218, i32 %descriptor, i1 false, i1 false)
  %cb = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %heap, %dx.types.ResourceProperties { i32 13, i32 32768 })
  %dead = call %dx.types.CBufRet.i32 @dx.op.cbufferLoadLegacy.i32(i32 59, %dx.types.Handle %cb, i32 0)
  %deadv = extractvalue %dx.types.CBufRet.i32 %dead, 0
  %live = call %dx.types.CBufRet.i32 @dx.op.cbufferLoadLegacy.i32(i32 59, %dx.types.Handle %cb, i32 1)
  %livev = extractvalue %dx.types.CBufRet.i32 %live, 0
  %used = add i32 %livev, 1
  ret void
}
'''

TEXTURE_IR = '''%dx.types.Handle = type { i8* }
; BindlessBuffer texture byte r/o T0 t0 1
define void @main() {
  %raw = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 0, i32 0, i32 0, i1 false)
  %base = lshr i32 %material, 4
  %addr = add i32 %base, 32
  %load = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %raw, i32 %addr, i32 0, i8 15, i32 4)
  %v = extractvalue %dx.types.ResRet.i32 %load, 0
  %handle = call %dx.types.Handle @dx.op.createHandleFromHeap(i32 218, i32 %v, i1 false, i1 false)
  %used = add i32 %v, 1
  ret void
}
'''


def layout():
    return MaterialTailLayout(32, ('BaseMap', 'DetailMap'), ('Mask',))


class MaterialRemovalTests(unittest.TestCase):
    def props(self):
        return [
            {'name': 'Dead', 'offset': 0, 'byte_size': 16},
            {'name': 'Live', 'offset': 16, 'byte_size': 16},
        ]

    def test_plan_compacts_parameters_and_tail_identity(self):
        result = plan_removal_layout(layout(), self.props(), [
            {'action': 'parameter', 'name': 'Dead'},
            {'action': 'texture', 'name': 'DetailMap'},
        ])
        self.assertEqual(result.removed_parameters, ('Dead',))
        self.assertEqual(result.removed_textures, ('DetailMap',))
        self.assertEqual(result.new.parameter_bytes, 16)
        self.assertEqual(result.new.texture_names, ('BaseMap',))
        self.assertEqual(result.parameters[0].name, 'Live')
        self.assertEqual(result.parameters[0].new_offset, 0)
        self.assertEqual(result.offset_in(16), 0)
        self.assertEqual(result.offset_in(32), 16)

    def test_used_parameter_is_not_proven_removable(self):
        result = plan_removal_layout(layout(), self.props(), [{'action': 'parameter', 'name': 'Live'}])
        with self.assertRaisesRegex(EditError, '读取偏移'):
            prove_shader_removal(IR, result)

    def test_unused_parameter_can_be_remapped_and_removed(self):
        result = plan_removal_layout(layout(), self.props(), [{'action': 'parameter', 'name': 'Dead'}])
        proof = prove_shader_removal(IR, result)
        edited, report = remap_shader_reads(IR, result)
        self.assertEqual(proof['scope'], 'bounded_dxil_consumers')
        self.assertEqual(report['proof']['layout_digest'], result.digest)
        self.assertNotIn('%dead = call', edited)
        self.assertNotIn('%deadv = extractvalue', edited)
        self.assertIn('i32 0)', edited)

    def test_unknown_external_handle_consumer_blocks_proof(self):
        result = plan_removal_layout(layout(), self.props(), [{'action': 'parameter', 'name': 'Dead'}])
        unknown = IR.replace('  ret void', '  call void @escape(%dx.types.Handle %cb)\n  ret void')
        with self.assertRaisesRegex(EditError, '覆盖不足'):
            prove_shader_removal(unknown, result)

    def test_deleted_texture_or_unknown_tail_is_blocked(self):
        result = plan_removal_layout(layout(), self.props(), [{'action': 'texture', 'name': 'BaseMap'}])
        with self.assertRaisesRegex(EditError, '读取偏移|未知材质尾部'):
            prove_shader_removal(TEXTURE_IR, result)

    def test_static_heap_and_instance_live_words_follow_field_identity(self):
        from mmtr_editor.material_addresses import material_reads
        base = ('; UserMaterialInstances texture byte r/o T0 t0 1\n'
            'define void @main() {\n'
            '  %raw = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 0, i32 0, i32 0, i1 false)\n'
            '  %stride = shl nuw nsw i32 %index, 5\n'
            '  %addr = or i32 %stride, 16\n'
            '  %load = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %raw, i32 %addr, i32 undef, i8 15, i32 4)\n'
            '  %value = extractvalue %dx.types.ResRet.i32 %load, 2\n'
            '  %used = add i32 %value, 1\n  ret void\n}\n')
        static = IR.replace('%heap = call %dx.types.Handle @dx.op.createHandleFromHeap(i32 218, i32 %descriptor, i1 false, i1 false)',
            '%heap = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 2, i32 0, i32 0, i1 false)').replace('i32 32768', 'i32 32')
        static = '; UserMaterial cbuffer NA NA CB0 cb0 1\n'+static
        constant = base.replace('i32 %addr, i32 undef', 'i32 16, i32 undef')
        result = plan_removal_layout(layout(), self.props(), [{'action': 'parameter', 'name': 'Dead'}])
        for source, expected in ((IR, 0), (static, 0), (base, 8), (constant, 8)):
            with self.subTest(source=source):
                edited, _ = remap_shader_reads(source, result)
                reads = material_reads(edited, include_static=True)
                self.assertEqual([e['offset'] for r in reads for e in r['extracts']], [expected])
                for read in reads:
                    if read['stride'] is not None:
                        self.assertEqual(read['stride']['bytes'], 16)
                if source == base:
                    self.assertNotIn('%addr = or ', edited)
        with self.assertRaisesRegex(EditError, '实例步长'):
            prove_shader_removal(base.replace('i32 %index, 5', 'i32 %index, 6'), result)

    def test_middle_texture_and_gpbf_live_words_move_without_parameter_compaction(self):
        from test_material_addresses import cb_ir
        from mmtr_editor.material_addresses import material_reads
        old = MaterialTailLayout(32, ('BaseMap', 'Unused', 'LastMap'), ('GP0', 'GP1'))
        result = plan_removal_layout(old, self.props(), [{'action': 'texture', 'name': 'Unused'}])
        source = cb_ir(2, (0, 2, 3)).replace('  ret void',
            '  %a = add i32 %field0, 1\n  %b = add i32 %field2, 1\n  %c = add i32 %field3, 1\n  ret void')
        edited, _ = remap_shader_reads(source, result)
        actual = {e['ssa']: e['offset'] for r in material_reads(edited) for e in r['extracts']}
        self.assertEqual(actual, {'%field0': 32, '%field2': 36, '%field3': 40})
        self.assertEqual(result.parameters[1].new_offset, 16)
        self.assertEqual(result.offset_in(48), 44)

    def test_tail_delete_and_row_packing_preserve_parameter_words(self):
        old = MaterialTailLayout(64, (), ())
        properties = [dict(name='A', offset=0, byte_size=12), dict(name='Dead', offset=16, byte_size=4),
                      dict(name='B', offset=32, byte_size=8), dict(name='C', offset=48, byte_size=4)]
        result = plan_removal_layout(old, properties, [{'action': 'parameter', 'name': 'Dead'}])
        self.assertEqual([(p.name, p.new_offset) for p in result.parameters], [('A', 0), ('B', 16), ('C', 24)])
        self.assertEqual(result.offset_in(36), 20)
        with self.assertRaisesRegex(EditError, '填充'):
            result.offset_in(28)
        tail = plan_removal_layout(layout(), self.props(), [{'action': 'parameter', 'name': 'Live'}])
        self.assertEqual(tail.new.parameter_bytes, 16)
        self.assertEqual(tail.offset_in(0), 0)

    def test_internal_call_annotation_alias_and_indirect_handle_are_blocked(self):
        result = plan_removal_layout(layout(), self.props(), [{'action': 'parameter', 'name': 'Dead'}])
        call = IR.replace('  ret void', '  call void @helper(i32 1)\n  ret void')+'\ndefine void @helper(i32 %v) {\n  ret void\n}\n'
        alias = IR.replace('  ret void',
            '  %other = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %cb, %dx.types.ResourceProperties { i32 11, i32 0 })\n'
            '  %dim = call %dx.types.Dimensions @dx.op.getDimensions(i32 72, %dx.types.Handle %other, i32 0)\n  ret void')
        for source in (call, alias):
            with self.assertRaisesRegex(EditError, '覆盖不足'):
                prove_shader_removal(source, result)

    def test_numeric_ssa_and_implicit_block_names_survive_instruction_removal(self):
        source = IR
        for index, name in enumerate(('heap', 'cb', 'dead', 'deadv', 'live', 'livev', 'used'), 1):
            source = source.replace('%'+name+' ', '%'+str(index)+' ').replace('%'+name+',', '%'+str(index)+',')
        result = plan_removal_layout(layout(), self.props(), [{'action': 'parameter', 'name': 'Dead'}])
        edited, _ = remap_shader_reads(source, result)
        self.assertNotRegex(edited, r'%\d+\b')
        self.assertIn('%mmtrRemovalSSA6 = extractvalue', edited)
        loop = 'define void @main() {\n  br label %1\n; <label>:1 ; preds = %0\n  %2 = phi i32 [ 0, %0 ]\n  ret void\n}'
        renamed = name_numeric_locals(loop)
        self.assertIn('mmtrRemovalSSA0:', renamed)
        self.assertIn('mmtrRemovalSSA1:', renamed)
        self.assertIn('[ 0, %mmtrRemovalSSA0 ]', renamed)

    def test_each_shader_identity_is_covered_while_duplicate_payloads_are_analyzed_once(self):
        from types import SimpleNamespace
        material = dict(properties=self.props(), mmtrs=[])
        doc = SimpleNamespace(current_info=lambda: {'shaders': [dict(index=i, stage='ps') for i in range(3)]},
                              shader_bytes=lambda i: b'one' if i < 2 else b'two')
        calls, progress = [], []
        with patch('mmtr_editor.material_extension.current_layout', return_value=layout()):
            report = analyze_material_removal(doc, material, [{'action': 'parameter', 'name': 'Dead'}],
                lambda payload: calls.append(payload) or IR,
                progress=lambda *args: progress.append(args))
        self.assertTrue(report['eligible'])
        self.assertEqual(calls, [b'one', b'two'])
        self.assertEqual([s['shader_index'] for s in report['shaders']], [0, 1, 2])
        self.assertEqual(progress[-1], ('analyzing_removal', 3, 3))
        self.assertFalse(report['coverage']['deletion_supported'])
        with self.assertRaisesRegex(EditError, 'MMTRS'):
            analyze_material_removal(doc, {**material, 'mmtrs': [[1, 0]]}, [], lambda _: IR)


# 只借用自有可执行夹具模块，不导入其TestCase，防止unittest重复发现。
import test_material_batch as native_fixture


@unittest.skipUnless(native_fixture.NATIVE_DXC.is_file(), 'native DXC unavailable')
class NativeRemovalPreviewTests(unittest.TestCase):
    """真实隔离DXC/后台RPC验证无作用字段与已使用字段，资产始终留在内存。"""
    @classmethod
    def setUpClass(cls):
        native_fixture.NativeMaterialBatchTests.setUpClass()

    def session(self):
        from mmtr_editor.rpc import RpcSession
        session = RpcSession()
        session.dll = str(native_fixture.NATIVE_DXC)
        session.service = native_fixture.NativeMaterialBatchTests().service()
        self.addCleanup(session.close)
        return session

    def wait(self, session, job):
        from mmtr_editor.material_jobs import TERMINAL
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            status = session.jobs.status(job['job_id'])
            if status['state'] in TERMINAL:
                self.assertEqual(status['state'], 'succeeded', status)
                return session.jobs.result(job['job_id'], limit=1)['result']
            time.sleep(.01)
        self.fail('native preview did not finish')

    def test_native_unused_extension_is_eligible_used_texture_is_not_and_sources_stay_unchanged(self):
        session = self.session()
        actions = [dict(action='parameter', name='UnusedP', byte_size=4), dict(action='texture', name='UnusedMap')]
        session.service.handle(dict(op='material_apply', actions=actions, record_index=0, dll=session.dll))
        before = session.service.doc.build()[0], session.service.mdf.build()[0]
        job = session.handle(dict(op='material_removal_preview', request_key='unused', material_index=0,
            actions=[dict(action='parameter', name='UnusedP'), dict(action='texture', name='UnusedMap')]))
        report = self.wait(session, job)
        self.assertTrue(report['eligible'], report)
        self.assertEqual((report['shader_count'], report['shaders_analyzed']), (2, 1))
        self.assertEqual(report['shaders_total'], 2)
        self.assertEqual(report['shaders_next_offset'], 1)
        self.assertEqual(report['plan']['target_materials'], [0, 1])
        self.assertIsNotNone(session.jobs.status(job['job_id'])['worker_pid'])
        used = session.handle(dict(op='material_removal_preview', request_key='used', material_index=0,
            actions=[dict(action='texture', name='BaseMap')]))
        report = self.wait(session, used)
        self.assertFalse(report['eligible'])
        self.assertEqual(report['failures_total'], 2)
        self.assertEqual(session.revision, 0)
        self.assertEqual(before, (session.service.doc.build()[0], session.service.mdf.build()[0]))
        # 原有只读统计RPC仍工作，且没有局部导入遮蔽master_matches。
        usage = session.handle(dict(op='mmtr_material_usage', material_index=0, dll=session.dll))
        self.assertEqual(usage['complete'], dict(parameters=True, textures=True))
        self.assertEqual(session.revision, 0)

    def test_native_numeric_ssa_remap_assembles_and_validates_in_isolated_process(self):
        from mmtr_editor.dxc_process import compile_hlsl
        import tempfile
        code = r'''
import json,sys
from mmtr_editor.dxil_assembly import DxilAssembly,retain_reflection
from mmtr_editor.material_removal import plan_removal_layout,remap_shader_reads
from mmtr_editor.material_addresses import MaterialTailLayout,material_reads
from pathlib import Path
request=json.load(sys.stdin)
compiler=DxilAssembly(request['dll'])
try:
    original=Path(request['payload']).read_bytes()
    ir=compiler.disassemble(original)
    old=MaterialTailLayout(32,(),())
    layout=plan_removal_layout(old,[dict(name='Dead',offset=0,byte_size=16),dict(name='Live',offset=16,byte_size=16)],
                              [dict(action='parameter',name='Dead')])
    edited,report=remap_shader_reads(ir,layout)
    compiled,_=compiler.assemble(edited)
    # 此测试仅改取值地址，声明/绑定未变；保留原STAT供实际载荷的命名来源复查。
    # 完整删除执行器必须另行更新声明和STAT，不能沿用这里的旧反射。
    validated,_=compiler.validate(retain_reflection(compiled,original))
    actual=compiler.disassemble(validated)
    offsets=sorted({e['offset'] for r in material_reads(actual,include_static=True) for e in r['extracts']})
    print(json.dumps(dict(offsets=offsets,size=len(validated),declarations_updated=report['declarations_updated'])))
finally:
    compiler.close()
'''
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory)/'source.dxbc'
            shader = 'cbuffer UserMaterial : register(b0) { float4 Dead; float4 Live; }; float4 main() : SV_Target { return Live; }'
            payload.write_bytes(compile_hlsl(str(native_fixture.NATIVE_DXC), shader, 'main', 'ps_6_6')[0])
            result = subprocess.run([sys.executable, '-c', code], input=json.dumps(
                dict(dll=str(native_fixture.NATIVE_DXC), payload=str(payload))).encode(),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            report = json.loads(result.stdout)
            self.assertEqual(report['offsets'], [0, 4, 8, 12])
            self.assertGreater(report['size'], 0)
            self.assertFalse(report['declarations_updated'])


if __name__ == '__main__':
    unittest.main()
