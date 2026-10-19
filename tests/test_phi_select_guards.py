"""Guard-aware select reduction must preserve clipping and derivative behavior."""
from pathlib import Path
import unittest

from mmtr_editor.verify_ir import compare_dataflow
from mmtr_editor.reconstruct import reconstruct_hlsl
from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.model import interface_chunks


IR = '''define void @main() {
  %a = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)
  %c = fcmp olt float %a, 1.000000e+00
  br i1 %c, label %yes, label %no
yes:
  %s = select i1 %c, float %a, float 1.000000e+00
  %t = fsub float %s, 5.000000e-01
  br label %join
no:
  br label %join
join:
  %p = phi float [ %t, %yes ], [ %a, %no ]
  %kill = fcmp ole float %p, 0.000000e+00
  call void @dx.op.discard(i32 82, i1 %kill)
  ret void
}
'''


class PhiSelectGuardTests(unittest.TestCase):
    def test_true_and_false_edge_select_reductions(self):
        simplified = IR.replace('%t = fsub float %s,', '%t = fsub float %a,')
        self.assertTrue(compare_dataflow(IR, simplified)['equal'])
        false_edge = IR.replace('br i1 %c, label %yes, label %no',
                                'br i1 %c, label %no, label %yes')
        simplified = false_edge.replace('%t = fsub float %s,',
                                       '%t = fsub float 1.000000e+00,')
        self.assertTrue(compare_dataflow(false_edge, simplified)['equal'])

    def test_wrong_arm_changed_guard_and_changed_clip_are_detected(self):
        for changed in (
            IR.replace('%t = fsub float %s,', '%t = fsub float 1.000000e+00,'),
            IR.replace('br i1 %c, label %yes, label %no',
                       'br i1 %c, label %no, label %yes'),
            IR.replace('fcmp ole float %p,', 'fcmp olt float %p,'),
            IR.replace('float %a, float 1.000000e+00',
                       'float 2.000000e+00, float 1.000000e+00'),
        ):
            with self.subTest(changed=changed):
                self.assertFalse(compare_dataflow(IR, changed)['equal'])

    def test_unproven_condition_and_derivative_context_remain_distinct(self):
        unrelated = IR.replace('%s = select i1 %c,',
                               '%other = fcmp olt float %a, 0.000000e+00\n'
                               '  %s = select i1 %other,')
        simplified = unrelated.replace('%t = fsub float %s,', '%t = fsub float %a,')
        self.assertFalse(compare_dataflow(unrelated, simplified)['equal'])
        derivative = IR.replace('yes:\n',
                                'yes:\n  %d = call float @dx.op.unary.f32(i32 83, float %a)\n')
        moved = derivative.replace('  %d = call float @dx.op.unary.f32(i32 83, float %a)\n', '')
        moved = moved.replace('no:\n',
                              'no:\n  %d = call float @dx.op.unary.f32(i32 83, float %a)\n')
        self.assertFalse(compare_dataflow(derivative, moved)['equal'])


# 自有标量选择反例，单线程纯CPU；只规范化值选择，不移动读取、导数或discard执行时机。
class PixelScalarChoiceTests(unittest.TestCase):
    def pair(self, scalar='float', yes='%a', no='%b'):
        prefix = ('define void @main() {\n'
                  '  %a = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)\n'
                  '  %b = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 1, i32 undef)\n'
                  '  %c = fcmp olt float %a, %b\n')
        if scalar == 'i32':
            prefix += '  %x = bitcast float %a to i32\n  %y = bitcast float %b to i32\n'
            yes, no = '%x', '%y'
        elif scalar == 'half':
            prefix += '  %x = fptrunc float %a to half\n  %y = fptrunc float %b to half\n'
            yes, no = '%x', '%y'
        output = ('  %out = fpext half %p to float\n' if scalar == 'half' else '')
        output += ('  call void @dx.op.storeOutput.'+('i32' if scalar == 'i32' else 'f32')
                   +'(i32 5, i32 0, i32 0, i8 0, '+('i32' if scalar == 'i32' else 'float')
                   +' '+('%out' if scalar == 'half' else '%p')+')\n  ret void\n}\n')
        selected = prefix+f'  %p = select i1 %c, {scalar} {yes}, {scalar} {no}\n'+output
        merged = (prefix+'  br i1 %c, label %yes, label %no\nyes:\n  br label %join\n'
                  +'no:\n  br label %join\njoin:\n'+f'  %p = phi {scalar} [ {yes}, %yes ], [ {no}, %no ]\n'+output)
        return selected, merged

    def test_scalar_select_and_phi_preserve_exact_values(self):
        for scalar in ('float', 'half', 'i32'):
            with self.subTest(scalar=scalar):
                a, b = self.pair(scalar)
                self.assertTrue(compare_dataflow(a, b)['equal'])
                self.assertTrue(compare_dataflow(b, a)['equal'])

    def test_changed_condition_arm_and_undefined_value_are_detected(self):
        a, b = self.pair()
        for changed in (b.replace('fcmp olt', 'fcmp ole'),
                        b.replace('[ %a, %yes ], [ %b, %no ]', '[ %b, %yes ], [ %a, %no ]'),
                        b.replace('[ %a, %yes ]', '[ undef, %yes ]')):
            with self.subTest(changed=changed):
                self.assertFalse(compare_dataflow(a, changed)['equal'])

    def test_nested_complementary_choices_remain_exact(self):
        a, b = self.pair()
        a = a.replace('  %p = select i1 %c, float %a, float %b\n',
                      '  %not = xor i1 %c, true\n'
                      '  %q = select i1 %not, float %b, float %a\n'
                      '  %p = select i1 %c, float %a, float %q\n')
        self.assertTrue(compare_dataflow(a, b)['equal'])

    def test_float_arithmetic_is_not_reassociated(self):
        a, _ = self.pair()
        prefix = '  %t = fadd float %a, %b\n  %v = fadd float %t, 1.000000e+00\n'
        a = a.replace('  %p = select', prefix+'  %p = select').replace('float %a, float %b\n', 'float %v, float %b\n')
        changed = a.replace(prefix, '  %t = fadd float %b, 1.000000e+00\n  %v = fadd float %a, %t\n')
        self.assertFalse(compare_dataflow(a, changed)['equal'])

    def test_derivative_execution_and_prior_discard_remain_distinct(self):
        _, a = self.pair()
        derivative = '  %d = call float @dx.op.unary.f32(i32 83, float %a)\n'
        a = a.replace('yes:\n', 'yes:\n'+derivative).replace('[ %a, %yes ]', '[ %d, %yes ]')
        moved = a.replace(derivative, '').replace('  br i1 %c,', derivative+'  br i1 %c,')
        self.assertFalse(compare_dataflow(a, moved)['equal'])
        selected, _ = self.pair()
        kill = '  call void @dx.op.discard(i32 82, i1 %c)\n'
        before = selected.replace('  %p = select', kill+derivative+'  %p = select')
        after = selected.replace('  %p = select', derivative+kill+'  %p = select')
        self.assertFalse(compare_dataflow(before, after)['equal'])


ROOT = Path('output/skin_runtime/shaders')
DLL = Path('C:/Software/Kiro/dxcompiler.dll')


@unittest.skipUnless((ROOT/'0069_PS_GBuffer.ll').exists() and DLL.exists(),
                     'Runtime skin fixtures/DXC unavailable')
class SkinClipRoundtripTests(unittest.TestCase):
    def test_four_complete_clip_entries_compile_and_match(self):
        compiler = Dxc(DLL)
        try:
            for index in (62, 64, 67, 69):
                with self.subTest(index=index):
                    fixture = ROOT/f'{index:04d}_PS_GBuffer.ll'
                    original = fixture.read_text(encoding='utf-8')
                    rebuilt = reconstruct_hlsl(original)
                    raw, _ = compiler.compile(rebuilt['source'], rebuilt['entry'], rebuilt['profile'])
                    compiled = compiler.disassemble(raw)
                    self.assertEqual(interface_chunks(fixture.with_suffix('.dxbc').read_bytes()),
                                     interface_chunks(raw))
                    self.assertFalse(reflection_differences(original, compiled))
                    self.assertTrue(compare_dataflow(original, compiled)['equal'])
                    # The final clipping comparison and dither table are observable.
                    changed = original.replace('fcmp fast ugt float', 'fcmp fast uge float', 1)
                    self.assertNotEqual(original, changed)
                    self.assertFalse(compare_dataflow(original, changed)['equal'])
                    changed = original.replace('[float 0.000000e+00, float 5.000000e-01',
                                               '[float 1.000000e+00, float 5.000000e-01', 1)
                    self.assertNotEqual(original, changed)
                    self.assertFalse(compare_dataflow(original, changed)['equal'])
        finally:
            compiler.close()


if __name__ == '__main__':
    unittest.main()
