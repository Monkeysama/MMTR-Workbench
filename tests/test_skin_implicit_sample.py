"""Implicit-LOD sampling must retain its quad guards and discard history."""
from pathlib import Path
import unittest

from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.model import interface_chunks
from mmtr_editor.reconstruct import ReconstructionError, reconstruct_hlsl
from mmtr_editor.verify_ir import compare_dataflow


SAMPLE = ('  %s = call %dx.types.ResRet.f32 @dx.op.sample.f32(i32 60, '
          '%dx.types.Handle %t, %dx.types.Handle %sam, float %u, float 0.000000e+00, '
          'float undef, float undef, i32 0, i32 0, i32 undef, float undef)\n')
PREFIX = '''define void @main() {
  %u = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)
  %t = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 0, i32 0, i32 0, i1 false)
  %sam = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 3, i32 0, i32 0, i1 false)
'''


class ImplicitSampleGuardTests(unittest.TestCase):
    def test_arguments_and_unused_sample_execution_are_checked(self):
        ir = PREFIX + SAMPLE + '  ret void\n}\n'
        self.assertTrue(compare_dataflow(ir, ir.replace('%s =', '%renamed ='))['equal'])
        for mutation in (ir.replace('float %u, float', 'float 1.000000e+00, float', 1),
                         ir.replace('i32 0, i32 0, i32 undef, float undef)', 'i32 1, i32 0, i32 undef, float undef)', 1),
                         ir.replace('i32 undef, float undef)', 'i32 undef, float 0.000000e+00)', 1),
                         ir.replace('%dx.types.Handle %sam, float', '%dx.types.Handle %t, float', 1),
                         ir.replace(SAMPLE, '')):
            with self.subTest(mutation=mutation):
                self.assertNotEqual(ir, mutation)
                self.assertFalse(compare_dataflow(ir, mutation)['equal'])
        for mutation in (ir.replace('i32 60,', 'i32 61,', 1),
                         ir.replace('sample.f32', 'sample.i32', 1),
                         ir.replace('i32 undef, float undef)', 'i32 undef)', 1)):
            with self.assertRaises(ReconstructionError):
                compare_dataflow(ir, mutation)

    def test_sample_cannot_move_across_discard(self):
        discard = '  call void @dx.op.discard(i32 82, i1 true)\n'
        left = PREFIX + SAMPLE + discard + '  ret void\n}\n'
        right = PREFIX + discard + SAMPLE + '  ret void\n}\n'
        report = compare_dataflow(left, right)
        self.assertFalse(report['equal'])
        self.assertIn('derivative_discard_masks', [x['kind'] for x in report['differences']])

    def test_sample_cannot_be_hoisted_out_of_divergent_branch(self):
        template = PREFIX + '''  %c = fcmp olt float %u, 1.000000e+00
BEFORE  br i1 %c, label %yes, label %end
yes:
INSIDE  br label %end
end:
  ret void
}
'''
        left = template.replace('BEFORE', '').replace('INSIDE', SAMPLE)
        right = template.replace('BEFORE', SAMPLE).replace('INSIDE', '')
        report = compare_dataflow(left, right)
        self.assertFalse(report['equal'])
        self.assertIn('derivatives', [x['kind'] for x in report['differences']])

    def test_implicit_sample_after_reporting_atomic_is_rejected(self):
        atomic = ('  %a = call i32 @dx.op.atomicBinOp.i32(i32 78, %dx.types.Handle %t, '
                  'i32 6, i32 0, i32 undef, i32 undef, i32 7)\n')
        ir = PREFIX + atomic + SAMPLE + '  ret void\n}\n'
        with self.assertRaises(ReconstructionError):
            compare_dataflow(ir, ir)


ROOT = Path('output/skin_runtime/pixel_coverage')
DLL = Path('C:/Software/Kiro/dxcompiler.dll')


@unittest.skipUnless(DLL.exists() and (ROOT/'0022/original.ll').exists(),
                     'Skin PS fixtures/DXC unavailable')
class ImplicitSampleRoundtripTests(unittest.TestCase):
    def test_real_shadow_and_meshlet_entries_preserve_interfaces_and_dataflow(self):
        compiler = Dxc(DLL)
        try:
            for index in (22, 23, 179, 180, 346, 347, 487, 488, 149, 312, 471, 555):
                with self.subTest(index=index):
                    fixture = ROOT/f'{index:04d}'/'original.ll'
                    ir = fixture.read_text(encoding='utf-8')
                    rebuilt = reconstruct_hlsl(ir)
                    self.assertIn('.Sample(', rebuilt['source'])
                    raw, _ = compiler.compile(rebuilt['source'], rebuilt['entry'], rebuilt['profile'])
                    actual = compiler.disassemble(raw)
                    self.assertEqual(interface_chunks(fixture.with_suffix('.dxbc').read_bytes()), interface_chunks(raw))
                    self.assertFalse(reflection_differences(ir, actual))
                    self.assertTrue(compare_dataflow(ir, actual)['equal'])
        finally:
            compiler.close()

    def test_coordinate_dimensions_and_mismatched_opcode_are_rejected(self):
        ir = (ROOT/'0022/original.ll').read_text(encoding='utf-8')
        for mutation in (ir.replace('float undef, float undef, i32 0, i32 0',
                                    'float 1.000000e+00, float undef, i32 0, i32 0', 1),
                         ir.replace('sample.f32(i32 60,', 'sample.f32(i32 62,', 1)):
            self.assertNotEqual(ir, mutation)
            with self.assertRaises(ReconstructionError):
                reconstruct_hlsl(mutation)


if __name__ == '__main__':
    unittest.main()
