"""Real material control flow, layout, and strict replacement regression checks."""
from pathlib import Path
import unittest
from mmtr_editor.reconstruct import reconstruct_hlsl, ReconstructionError
from mmtr_editor.material_hlsl import MaterialTranslator
from scripts.verify_volume_blend import verify, canonical_body

FIXTURE = Path('output/variation_scan/material_candidates/Simple_VolumeBlend/0024_PS_ShadowCast.ll')


@unittest.skipUnless(FIXTURE.exists(), 'Local material fixture missing')
class MaterialReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.ir = FIXTURE.read_text(encoding='utf-8')

    def test_exact_layout_matrices_and_branch_phi(self):
        translator = MaterialTranslator(self.ir)
        self.assertEqual(translator.buffers['SceneInfo'][7*4+3].expr, 'transposeViewInvMat[0][3]')
        self.assertEqual(translator.buffers['SceneInfo'][9*4+3].expr, 'transposeViewInvMat[2][3]')
        self.assertEqual(translator.buffers['SceneInfo'][22*4+2].expr, 'ZToLinear.z')
        result = translator.reconstruct()
        source = result['source']
        self.assertIn('packoffset(c22.w)', source)
        self.assertIn('ReadonlyDepth.Load(int3(v_32, v_33, 0))', source)
        self.assertIn('v_58 = edge_49_57_58', source)
        self.assertIn('v_58 = edge_54_57_58', source)
        self.assertIn('!(v_78 <= 0.000000e+00)', source)
        self.assertIn('discard', source)
        self.assertNotIn('phi_from', source)
        self.assertNotIn('goto', source)

    def test_unknown_op_loop_and_missing_phi_predecessor_rejected(self):
        fixtures = [self.ir.replace('i32 23, float %69', 'i32 999, float %69'),
                    self.ir.replace('br label %57', 'br label %0', 1),
                    self.ir.replace('[ %56, %54 ]', '[ %56, %999 ]')]
        for ir in fixtures:
            with self.assertRaises(ReconstructionError):
                reconstruct_hlsl(ir)

    def test_padding_or_vector_texture_rejected(self):
        with self.assertRaises(ReconstructionError):
            reconstruct_hlsl(self.ir.replace('%"class.Texture2D<float>"* undef', '%"class.TextureCube<float4>"* undef'))
        # SceneInfo c39 is outside its 624-byte declaration.
        with self.assertRaises(ReconstructionError):
            reconstruct_hlsl(self.ir.replace('%3, i32 7)', '%3, i32 39)'))

    def test_meaningful_operation_change_detected(self):
        self.assertNotEqual(canonical_body(self.ir), canonical_body(self.ir.replace('%38, %40', '%38, %39')))

    @unittest.skipUnless(Path('C:/Software/Kiro/dxcompiler.dll').exists(), 'DXC unavailable')
    def test_compile_reflection_operations_and_roundtrip(self):
        result = verify()
        self.assertFalse(result['interface_differences'])
        self.assertFalse(result['reflection_differences'])
        self.assertTrue(result['canonical_body_equal'])
        self.assertTrue(result['other_shaders_unchanged'])


if __name__ == '__main__':
    unittest.main()
