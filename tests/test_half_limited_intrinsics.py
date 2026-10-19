"""Bounded exact half intrinsic widening and rejection of nearby changes."""
import math
import struct
import unittest

from mmtr_editor.verify_ir import compare_dataflow


def program(body):
    return '''define void @main() {
  %x = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)
  %h = fptrunc float %x to half
'''+body+'''
  %result = fpext half %v to float
  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 0, float %result)
  ret void
}
'''


class HalfLimitedIntrinsicTests(unittest.TestCase):
    def test_saturation_lowering_retains_input_rounding_output_type_and_opcode(self):
        original = program('  %v = call half @dx.op.unary.f16(i32 7, half %h)')
        lowered = program('''  %f = fpext half %h to float
  %sat = call float @dx.op.unary.f32(i32 7, float %f)
  %v = fptrunc float %sat to half''')
        self.assertTrue(compare_dataflow(original,lowered)['equal'])
        for changed in (lowered.replace('float %f)','float %x)'),
                        lowered.replace('i32 7, float','i32 6, float'),
                        lowered.replace('float %result)','float %sat)')):
            self.assertFalse(compare_dataflow(original,changed)['equal'])

    def test_max_requires_exact_positive_normal_half_bound_and_final_rounding(self):
        original = program('  %v = call half @dx.op.binary.f16(i32 35, half 0xH211F, half %h)')
        # 0x211f is exactly 0.01000213623046875 at float32 and half widths.
        lowered = program('''  %f = fpext half %h to float
  %max = call float @dx.op.binary.f32(i32 35, float 0.01000213623046875, float %f)
  %v = fptrunc float %max to half''')
        self.assertTrue(compare_dataflow(original,lowered)['equal'])
        for changed in (lowered.replace('0.01000213623046875','0.01'),
                        lowered.replace('float %f)','float %x)'),
                        lowered.replace('i32 35,','i32 36,'),
                        lowered.replace('float %result)','float %max)')):
            self.assertFalse(compare_dataflow(original,changed)['equal'])
        for half,number in (('0000','0.0'),('8000','-0.0'),('0001','0.000000059604644775390625'),
                            ('BC00','-1.0'),('7C00','0x7FF0000000000000'),('7E00','0x7FF8000000000000')):
            self.assertFalse(compare_dataflow(original.replace('211F',half),lowered.replace('0.01000213623046875',number))['equal'])

    def test_every_half_encoding_preserves_saturate_and_positive_normal_max(self):
        def sat(value):
            if math.isnan(value) or value <= 0: return 0.0
            return 1.0 if value >= 1 else value
        bound = struct.unpack('>e',bytes.fromhex('211f'))[0]
        def maximum(value):
            return bound if math.isnan(value) or value <= bound else value
        for bits in range(65536):
            half = struct.unpack('>e',bits.to_bytes(2,'big'))[0]
            wide = struct.unpack('>f',struct.pack('>f',half))[0]
            for operation in (sat,maximum):
                direct = struct.pack('>e',operation(half))
                lifted = struct.pack('>e',struct.unpack('>f',struct.pack('>f',operation(wide)))[0])
                self.assertEqual(direct,lifted,(hex(bits),operation.__name__))


if __name__ == '__main__':
    unittest.main()
