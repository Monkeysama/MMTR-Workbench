"""Real skin arithmetic, sampling, CFG, and falsification of reconstruction checks."""
from pathlib import Path
import unittest
from mmtr_editor.reconstruct import reconstruct_hlsl, ReconstructionError
from mmtr_editor.verify_ir import compare_dataflow
from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.model import interface_chunks

ROOT = Path('output/skin_runtime')
FIXTURE = ROOT/'shaders/0061_PS_GBuffer.ll'
DLL = Path('C:/Software/Kiro/dxcompiler.dll')


@unittest.skipUnless(FIXTURE.exists(), 'Runtime skin fixture not extracted')
class SkinHlslTests(unittest.TestCase):
    def test_complete_sampled_entry_is_compilable_and_dataflow_matches(self):
        ir = FIXTURE.read_text(encoding='utf-8')
        r = reconstruct_hlsl(ir)
        self.assertEqual(r['instructions'], 1956)
        self.assertEqual(r['blocks'], 69)
        self.assertIn('[earlydepthstencil]', r['source'])
        self.assertIn('precise float', r['source'])
        self.assertIn('SampleGrad', r['source'])
        self.assertIn('SampleLevel', r['source'])
        self.assertIn('ByteAddressBuffer', r['source'])
        self.assertIn('ddx_coarse', r['source'])
        x = Dxc(DLL)
        try:
            raw, _ = x.compile(r['source'], r['entry'], r['profile'])
            compiled = x.disassemble(raw)
        finally:
            x.close()
        self.assertEqual(interface_chunks(FIXTURE.with_suffix('.dxbc').read_bytes()), interface_chunks(raw))
        self.assertFalse(reflection_differences(ir, compiled))
        self.assertTrue(compare_dataflow(ir, compiled)['equal'])

    def test_value_gradient_and_branch_mutations_are_detected(self):
        ir = FIXTURE.read_text(encoding='utf-8')
        mutations = [ir.replace('float %134, float %135, float undef', 'float %135, float %134, float undef', 1),
                     ir.replace('fcmp fast ogt', 'fcmp fast olt', 1),
                     ir.replace('i32 %122, i32 undef', 'i32 0, i32 undef', 1)]
        for changed in mutations:
            self.assertNotEqual(ir, changed)
            self.assertFalse(compare_dataflow(ir, changed)['equal'])

    def test_constant_table_contents_and_unknown_effects_are_checked(self):
        ir = (ROOT/'shaders/0062_PS_GBuffer.ll').read_text(encoding='utf-8')
        self.assertTrue(compare_dataflow(ir, ir)['equal'])
        changed = ir.replace('[float 0.000000e+00, float 5.000000e-01', '[float 1.000000e+00, float 5.000000e-01', 1)
        self.assertNotEqual(ir, changed)
        self.assertFalse(compare_dataflow(ir, changed)['equal'])
        with self.assertRaises(ReconstructionError):
            compare_dataflow(ir, ir.replace('ret void', 'call void @dx.op.unknownSideEffect(i32 999)\n  ret void'))

    def test_mismatched_sampling_dimensions_and_raw_mask_rejected(self):
        ir = FIXTURE.read_text(encoding='utf-8')
        mutations = [ir.replace('i32 undef, i8 15, i32 4)', 'i32 undef, i8 5, i32 4)', 1),
                     ir.replace('texture     f32          3d', 'texture     f32          2d', 1)]
        for changed in mutations:
            self.assertNotEqual(ir, changed)
            with self.assertRaises(ReconstructionError):
                reconstruct_hlsl(changed)


if __name__ == '__main__':
    unittest.main()
