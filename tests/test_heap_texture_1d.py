"""自有1D heap纹理的完整编译往返；验证维度拒绝与采样/Load输入被数据流检查覆盖。"""
from pathlib import Path
import re
import unittest

from mmtr_editor.dxc_process import compile_hlsl
from mmtr_editor.dxc import reflection_differences
from mmtr_editor.model import interface_chunks
from mmtr_editor.reconstruct import ReconstructionError, reconstruct_hlsl
from mmtr_editor.verify_ir import compare_dataflow


DLL = Path('C:/Software/Kiro/dxcompiler.dll')


# 每次测试独占隔离DXC worker；无游戏输入、GPU执行或安装，产物只在内存中比较。
@unittest.skipUnless(DLL.exists(), 'Local DXC unavailable')
class HeapTexture1DTests(unittest.TestCase):
    def compile_source(self, expression, element='float4'):
        source = ('SamplerState sourceSampler : register(s0);\n'
                  'float4 main(float2 uv:TEXCOORD0, nointerpolation uint index:TEXCOORD1):SV_Target {\n'
                  f' Texture1D<{element}> texture=ResourceDescriptorHeap[NonUniformResourceIndex(index)];\n'
                  f' return {expression};\n' + '}\n')
        return compile_hlsl(DLL, source, 'main', 'ps_6_6', timeout=20)

    def test_scalar_coordinates_offsets_and_gradients_roundtrip(self):
        for expression, element in (
            ('texture.Sample(sourceSampler, uv.x, 1)', 'float4'),
            ('texture.Sample(sourceSampler, cos(uv.x)+sin(uv.y), 1)', 'float4'),
            ('texture.Sample(sourceSampler, atan(uv.x), 1)', 'float4'),
            ('texture.Sample(sourceSampler, acos(uv.x), 1)', 'float4'),
            ('texture.SampleLevel(sourceSampler, uv.x, uv.y, -1)', 'float4'),
            ('texture.SampleGrad(sourceSampler, uv.x, ddx(uv.x), ddy(uv.x), 2)', 'float4'),
            ('texture.Load(int2(int(uv.x), int(uv.y)), 1)', 'float4'),
            ('asfloat(texture.Load(int2(int(uv.x), int(uv.y))))', 'uint4'),
        ):
            with self.subTest(expression=expression):
                original, _, ir = self.compile_source(expression, element)
                rebuilt = reconstruct_hlsl(ir)
                self.assertIn('Texture1D<', rebuilt['source'])
                self.assertIn('NonUniformResourceIndex(', rebuilt['source'])
                binary, _, actual = compile_hlsl(DLL, rebuilt['source'], rebuilt['entry'], rebuilt['profile'], timeout=20)
                self.assertEqual(interface_chunks(original), interface_chunks(binary))
                self.assertEqual(reflection_differences(ir, actual), [])
                self.assertTrue(compare_dataflow(ir, actual)['equal'])

    def test_extra_coordinates_gradients_offsets_and_type_are_rejected(self):
        _, _, ir = self.compile_source('texture.SampleGrad(sourceSampler, uv.x, ddx(uv.x), ddy(uv.x), 2)')
        call = re.search(r'@dx.op.sampleGrad.f32\([^\n]+', ir)[0]
        # DXIL未使用的第二坐标/offset/梯度必须保持undef；改变注解不能把2D解释成1D。
        mutations = (
            call.replace('float undef', 'float 0.000000e+00', 1),
            call.replace('i32 undef', 'i32 0', 1),
            call.replace('float undef, float undef, float undef)', 'float 1.000000e+00, float undef, float undef)'),
        )
        for changed in mutations:
            self.assertNotEqual(call, changed)
            with self.assertRaises(ReconstructionError):
                reconstruct_hlsl(ir.replace(call, changed, 1))
        with self.assertRaises(ReconstructionError):
            reconstruct_hlsl(ir.replace('i32 1, i32 1033', 'i32 1, i32 1289', 1))

    def test_coordinate_descriptor_and_sampling_mutations_are_observable(self):
        _, _, ir = self.compile_source('texture.Sample(sourceSampler, uv.x, 1)')
        sample = re.search(r'@dx.op.sample.f32\([^\n]+', ir)[0]
        coord = re.search(r', float (%\d+), float undef', sample)[1]
        changed = ir.replace(sample, sample.replace('float '+coord, 'float 0.000000e+00'), 1)
        self.assertFalse(compare_dataflow(ir, changed)['equal'])
        heap = re.search(r'@dx.op.createHandleFromHeap\([^\n]+', ir)[0]
        changed = ir.replace(heap, re.sub(r'i32 %\d+', 'i32 0', heap, count=1), 1)
        self.assertNotEqual(changed, ir)
        self.assertFalse(compare_dataflow(ir, changed)['equal'])
        changed = ir.replace(sample, sample.replace('i32 1, i32 undef', 'i32 2, i32 undef'), 1)
        self.assertNotEqual(changed, ir)
        self.assertFalse(compare_dataflow(ir, changed)['equal'])


if __name__ == '__main__':
    unittest.main()
