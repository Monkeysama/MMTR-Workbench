"""Wave intrinsics retain their lane execution conditions and discard masks."""
from pathlib import Path
import unittest

from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.model import interface_chunks
from mmtr_editor.reconstruct import ReconstructionError, reconstruct_hlsl
from mmtr_editor.verify_ir import compare_dataflow

FIXTURES = Path('output/skin_runtime/shaders')
DLL = Path('C:/Software/Kiro/dxcompiler.dll')
INDICES = (*range(77, 81), *range(234, 238), *range(401, 405), *range(515, 519))


class WaveExecutionTests(unittest.TestCase):
    def test_wave_arguments_predicates_and_opcodes_are_checked(self):
        ir = '''define void @main() {
  %a = call i32 @dx.op.loadInput.i32(i32 4, i32 0, i32 0, i8 0, i32 undef)
  %x = call i32 @dx.op.waveReadLaneFirst.i32(i32 118, i32 %a)
  %c = icmp eq i32 %a, 1
  %y = call i32 @dx.op.waveAllOp(i32 135, i1 %c)
  ret void
}
'''
        self.assertTrue(compare_dataflow(ir, ir.replace('%x', '%renamed'))['equal'])
        for mutation in (ir.replace('i32 118, i32 %a', 'i32 118, i32 17'),
                         ir.replace('icmp eq', 'icmp ne')):
            self.assertFalse(compare_dataflow(ir, mutation)['equal'])
        for mutation in (ir.replace('i32 118', 'i32 117'), ir.replace('i32 135', 'i32 136')):
            with self.assertRaises(ReconstructionError):
                compare_dataflow(ir, mutation)

    def test_wave_cannot_move_across_discard(self):
        for operation in ('%x = call i32 @dx.op.waveReadLaneFirst.i32(i32 118, i32 7)',
                          '%x = call i32 @dx.op.waveAllOp(i32 135, i1 true)'):
            with self.subTest(operation=operation):
                wave = '  ' + operation + '\n'
                discard = '  call void @dx.op.discard(i32 82, i1 true)\n'
                left = 'define void @main() {\n' + wave + discard + '  ret void\n}\n'
                right = 'define void @main() {\n' + discard + wave + '  ret void\n}\n'
                report = compare_dataflow(left, right)
                self.assertFalse(report['equal'])
                self.assertIn('wave_discard_masks', [d['kind'] for d in report['differences']])

    def test_wave_execution_guard_cannot_change(self):
        template = '''define void @main() {
  %a = call i32 @dx.op.loadInput.i32(i32 4, i32 0, i32 0, i8 0, i32 undef)
  %c = icmp eq i32 %a, 1
BEFORE  br i1 %c, label %yes, label %no
yes:
INSIDE  br label %end
no:
  br label %end
end:
  ret void
}
'''
        wave = '  %x = call i32 @dx.op.waveReadLaneFirst.i32(i32 118, i32 %a)\n'
        left = template.replace('BEFORE', '').replace('INSIDE', wave)
        right = template.replace('BEFORE', wave).replace('INSIDE', '')
        self.assertFalse(compare_dataflow(left, right)['equal'])


@unittest.skipUnless(DLL.exists() and all((FIXTURES/f'{i:04d}_PS_GBuffer.ll').exists() for i in INDICES),
                     'Skin wave fixtures/DXC unavailable')
class SkinWaveRoundtripTests(unittest.TestCase):
    def test_sixteen_acyclic_wave_entries_compile_with_exact_interfaces_and_dataflow(self):
        compiler = Dxc(DLL)
        try:
            for index in INDICES:
                with self.subTest(index=index):
                    path = FIXTURES/f'{index:04d}_PS_GBuffer.ll'
                    original = path.read_text(encoding='utf-8')
                    result = reconstruct_hlsl(original)
                    raw, _ = compiler.compile(result['source'], result['entry'], result['profile'])
                    actual = compiler.disassemble(raw)
                    self.assertEqual(interface_chunks(path.with_suffix('.dxbc').read_bytes()), interface_chunks(raw))
                    self.assertFalse(reflection_differences(original, actual))
                    report = compare_dataflow(original, actual)
                    self.assertTrue(report['equal'])
                    self.assertEqual(report['wave_operation_counts'], [1, 1])
        finally:
            compiler.close()

if __name__ == '__main__':
    unittest.main()
