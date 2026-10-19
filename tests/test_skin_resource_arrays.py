"""Unbounded SRV arrays retain absolute register indices and nonuniform flags."""
from pathlib import Path
import re
import unittest

from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.model import interface_chunks
from mmtr_editor.reconstruct import ReconstructionError, reconstruct_hlsl
from mmtr_editor.verify_ir import compare_dataflow

FIXTURES = Path('output/skin_runtime/shaders')
DLL = Path('C:/Software/Kiro/dxcompiler.dll')
INDICES = (57, 58, 59, 60, 71, 72, 73, 74)


@unittest.skipUnless(DLL.exists() and all((FIXTURES/f'{i:04d}_PS_GBuffer.ll').exists() for i in INDICES),
                     'Skin resource-array fixtures/DXC unavailable')
class SkinResourceArrayTests(unittest.TestCase):
    def test_all_eight_unbounded_entries_compile_with_exact_interfaces_and_dataflow(self):
        compiler = Dxc(DLL)
        try:
            for index in INDICES:
                with self.subTest(index=index):
                    path = FIXTURES/f'{index:04d}_PS_GBuffer.ll'
                    original = path.read_text(encoding='utf-8')
                    result = reconstruct_hlsl(original)
                    self.assertIn('ByteAddressBuffer bindlessBuffer[] : register(t0, space1)', result['source'])
                    self.assertIn('Texture2D<float4> bindlessTexture2D[] : register(t0, space4)', result['source'])
                    self.assertIn('NonUniformResourceIndex(', result['source'])
                    raw, _ = compiler.compile(result['source'], result['entry'], result['profile'])
                    actual = compiler.disassemble(raw)
                    self.assertEqual(interface_chunks(path.with_suffix('.dxbc').read_bytes()), interface_chunks(raw))
                    self.assertFalse(reflection_differences(original, actual))
                    self.assertTrue(compare_dataflow(original, actual)['equal'])
        finally:
            compiler.close()

    def test_nonuniform_flag_index_and_range_changes_are_detected(self):
        ir = (FIXTURES/'0057_PS_GBuffer.ll').read_text(encoding='utf-8')
        pattern = r'(@dx.op.createHandle\(i32 57, i8 0, i32 4, i32 )(%\d+)(, i1 true\))'
        uniform = re.sub(pattern, lambda m: m[1] + m[2] + ', i1 false)', ir, count=1)
        index = re.sub(pattern, lambda m: m[1] + '17' + m[3], ir, count=1)
        for changed in (uniform, index):
            self.assertNotEqual(ir, changed)
            self.assertFalse(compare_dataflow(ir, changed)['equal'])
        count = ir.replace('i32 1, i32 0, i32 -1, i32 11', 'i32 1, i32 0, i32 1, i32 11', 1)
        self.assertNotEqual(ir, count)
        with self.assertRaises(ReconstructionError):
            reconstruct_hlsl(count)
        space = ir.replace('i32 4, i32 0, i32 -1, i32 2', 'i32 5, i32 0, i32 -1, i32 2', 1)
        self.assertNotEqual(ir, space)
        with self.assertRaises(ReconstructionError):
            reconstruct_hlsl(space)

    def test_array_lower_bound_and_singleton_index_validation(self):
        compiler = Dxc(DLL)
        try:
            source = '''ByteAddressBuffer buffers[] : register(t7, space1);
float4 main(nointerpolation uint index : TEXCOORD0) : SV_Target {
    return asfloat(buffers[NonUniformResourceIndex(index)].Load(0)).xxxx;
}'''
            for profile in ('ps_6_2', 'ps_6_6'):
                with self.subTest(profile=profile):
                    original, _ = compiler.compile(source, 'main', profile)
                    ir = compiler.disassemble(original)
                    reconstructed = reconstruct_hlsl(ir)
                    actual, _ = compiler.compile(reconstructed['source'], reconstructed['entry'], reconstructed['profile'])
                    compiled = compiler.disassemble(actual)
                    self.assertFalse(reflection_differences(ir, compiled))
                    self.assertEqual(interface_chunks(original), interface_chunks(actual))
                    self.assertTrue(compare_dataflow(ir, compiled)['equal'])
        finally:
            compiler.close()
        ir = (FIXTURES/'0057_PS_GBuffer.ll').read_text(encoding='utf-8')
        singleton = '@dx.op.createHandle(i32 57, i8 0, i32 7, i32 4, i1 false)'
        for changed in (singleton.replace('i32 4,', 'i32 5,'), singleton.replace('false', 'true')):
            mutation = ir.replace(singleton, changed, 1)
            self.assertNotEqual(ir, mutation)
            with self.assertRaises(ReconstructionError):
                reconstruct_hlsl(mutation)


if __name__ == '__main__':
    unittest.main()
