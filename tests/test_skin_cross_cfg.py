"""Shared scalar clipping tails and preservation of discarded/helper lane masks."""
from pathlib import Path
import unittest

from mmtr_editor.reconstruct import ReconstructionError, reconstruct_hlsl
from mmtr_editor.verify_ir import compare_dataflow
from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.model import interface_chunks


class DiscardMaskTests(unittest.TestCase):
    def test_discard_cannot_move_across_a_derivative_output_or_atomic(self):
        prefix = '''define void @main() {
  %a = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)
  %h = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 1, i32 0, i32 0, i1 false)
'''
        discard = '  call void @dx.op.discard(i32 82, i1 true)\n'
        derivative = '  %d = call float @dx.op.unary.f32(i32 83, float %a)\n'
        output = '  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 0, float %a)\n'
        atomic = ('  %result = call i32 @dx.op.atomicBinOp.i32(i32 78, %dx.types.Handle %h, '
                  'i32 6, i32 0, i32 undef, i32 undef, i32 7)\n')
        for operation in (derivative, output, atomic):
            with self.subTest(operation=operation):
                left = prefix + operation + discard + '  ret void\n}\n'
                right = prefix + discard + operation + '  ret void\n}\n'
                report = compare_dataflow(left, right)
                self.assertFalse(report['equal'])
                self.assertTrue(any('discard_masks' in d['kind'] for d in report['differences']))

    def test_repeated_discard_is_idempotent_but_predicate_changes_are_not(self):
        ir = '''define void @main() {
  %a = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)
  %c = fcmp ole float %a, 0.000000e+00
  call void @dx.op.discard(i32 82, i1 %c)
  ret void
}
'''
        discard = '  call void @dx.op.discard(i32 82, i1 %c)\n'
        repeated = ir.replace(discard, discard + discard)
        self.assertTrue(compare_dataflow(ir, repeated)['equal'])
        self.assertFalse(compare_dataflow(ir, repeated.replace('fcmp ole', 'fcmp olt'))['equal'])

    def test_phi_comparison_split_preserves_unordered_predicate(self):
        left = '''define void @main() {
  %a = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)
  %c = fcmp olt float %a, 1.000000e+00
  br i1 %c, label %yes, label %no
yes:
  br label %join
no:
  br label %join
join:
  %p = phi float [ %a, %yes ], [ 1.000000e+00, %no ]
  %kill = fcmp ugt float %p, 0.000000e+00
  call void @dx.op.discard(i32 82, i1 %kill)
  ret void
}
'''
        right = '''define void @main() {
  %a = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)
  %c = fcmp olt float %a, 1.000000e+00
  br i1 %c, label %yes, label %no
yes:
  %x = fcmp ugt float %a, 0.000000e+00
  call void @dx.op.discard(i32 82, i1 %x)
  br label %join
no:
  %y = fcmp ugt float 1.000000e+00, 0.000000e+00
  call void @dx.op.discard(i32 82, i1 %y)
  br label %join
join:
  ret void
}
'''
        self.assertTrue(compare_dataflow(left, right)['equal'])
        self.assertFalse(compare_dataflow(left, right.replace('fcmp ugt', 'fcmp ogt'))['equal'])


ROOT = Path('output/skin_runtime/shaders')
DLL = Path('C:/Software/Kiro/dxcompiler.dll')


@unittest.skipUnless((ROOT/'0386_PS_GBuffer.ll').exists() and DLL.exists(),
                     'Skin cross-CFG fixtures/DXC unavailable')
class CrossCfgRoundtripTests(unittest.TestCase):
    def test_shared_clipping_tails_compile_with_exact_interfaces_and_masks(self):
        compiler = Dxc(DLL)
        try:
            for index in (215, 386):
                with self.subTest(index=index):
                    fixture = ROOT/f'{index:04d}_PS_GBuffer.ll'
                    original = fixture.read_text(encoding='utf-8')
                    result = reconstruct_hlsl(original)
                    self.assertEqual(len(result['duplicated_scalar_blocks']), 2)
                    raw, _ = compiler.compile(result['source'], result['entry'], result['profile'])
                    compiled = compiler.disassemble(raw)
                    self.assertEqual(interface_chunks(raw), interface_chunks(fixture.with_suffix('.dxbc').read_bytes()))
                    self.assertFalse(reflection_differences(original, compiled))
                    self.assertTrue(compare_dataflow(original, compiled)['equal'])
                    mutation = compiled.replace('fcmp ugt float', 'fcmp uge float', 1)
                    self.assertNotEqual(compiled, mutation)
                    self.assertFalse(compare_dataflow(original, mutation)['equal'])
        finally:
            compiler.close()

    def test_shared_quad_block_remains_rejected(self):
        original = (ROOT/'0386_PS_GBuffer.ll').read_text(encoding='utf-8')
        # The shared block has two predecessors; duplicating a derivative there
        # would run two different quads, so scalar-tail support must reject it.
        changed = original.replace('  call void @dx.op.discard(i32 82, i1 true)',
                                   '  %999999 = call float @dx.op.unary.f32(i32 83, float 1.000000e+00)\n'
                                   '  call void @dx.op.discard(i32 82, i1 true)', 1)
        self.assertNotEqual(original, changed)
        with self.assertRaises(ReconstructionError):
            reconstruct_hlsl(changed)


if __name__ == '__main__':
    unittest.main()
