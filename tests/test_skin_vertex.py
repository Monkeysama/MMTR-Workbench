"""Vertex stage metadata, semantics, computation and roundtrip boundaries."""
import json
from pathlib import Path
import tempfile
import unittest

from mmtr_editor.dxc import Dxc,reflection_differences
from mmtr_editor.model import Document,interface_chunks
from mmtr_editor.reconstruct import reconstruct_hlsl,shader_interface,ReconstructionError
from mmtr_editor.verify_ir import compare_dataflow

ROOT=Path('output/skin_runtime/stage_inventory')
DLL=Path('C:/Software/Kiro/dxcompiler.dll')


@unittest.skipUnless((ROOT/'inventory.json').exists() and DLL.exists(),'Local original skin IR/DXC required')
class VertexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory=json.loads((ROOT/'inventory.json').read_text(encoding='utf-8'))
        cls.fixture=next(x for x in cls.inventory['entries'] if x['index']==131)
        cls.ir=Path(cls.fixture['ir']).read_text(encoding='utf-8')

    def test_stage_root_not_unrelated_metadata_controls_profile(self):
        ir=self.ir+'\n!99999 = !{!"ps", i32 6, i32 6}\n'
        self.assertEqual(shader_interface(ir)[2],'vs_6_2')
        for changed in (self.ir.replace('!"vs", i32 6','!"cs", i32 6'),
                        self.ir.replace('!"vs", i32 6','!"ms", i32 6'),
                        self.ir.replace('i64 256','i64 264'),
                        self.ir.replace('i64 256','i64 320'),
                        self.ir.replace('!"POSITION", i8 9, i8 0','!"POSITION", i8 9, i8 1',1),
                        self.ir.replace('!12, i8 0, i32 1, i8 3','!12, i8 2, i32 1, i8 3',1)):
            with self.assertRaises(ReconstructionError):reconstruct_hlsl(changed)

    def test_vertex_forbids_pixel_effects_derivatives_and_writes_even_unused(self):
        for instruction in ('call void @dx.op.discard(i32 82, i1 true)',
                            '%99999 = call float @dx.op.unary.f32(i32 83, float 0.0)',
                            '%99999 = call i32 @dx.op.sampleIndex.i32(i32 90)',
                            '%99999 = call i32 @dx.op.quadOp.i32(i32 123, i32 0, i8 0)',
                            'call void @dx.op.rawBufferStore.i32(i32 140)',
                            '%99999 = call i32 @dx.op.atomicBinOp.i32(i32 78)'):
            with self.assertRaises(ReconstructionError):
                reconstruct_hlsl(self.ir.replace('  ret void','  '+instruction+'\n  ret void'))

    def test_original_vertex_interfaces_reflection_and_computation(self):
        c=Dxc(DLL)
        try:
            d=Document.open(self.inventory['source'])
            original=Document.open(self.inventory['source'])
            tested=(131,288,455,133,3,24,75,213,141,143)
            for index in tested:
                item=next(x for x in self.inventory['entries'] if x['index']==index)
                ir=Path(item['ir']).read_text(encoding='utf-8')
                r=reconstruct_hlsl(ir)
                self.assertEqual(r['profile'],item['profile'])
                self.assertNotIn('[earlydepthstencil]',r['source'])
                binary,_=c.compile(r['source'],r['entry'],r['profile']);actual=c.disassemble(binary)
                self.assertEqual(interface_chunks(d.shader_bytes(index)),interface_chunks(binary))
                self.assertFalse(reflection_differences(ir,actual))
                self.assertTrue(compare_dataflow(ir,actual)['equal'])
                d.replace_shader(index,binary)
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'vertex.mmtr.240906211';d.export(path);reread=Document.open(path)
                self.assertEqual([s['index'] for s in d.info['shaders'] if reread.shader_bytes(s['index'])!=original.shader_bytes(s['index'])],sorted(tested))
                self.assertFalse(reread.integrity['shader_trailer_mismatches'])
        finally:c.close()

    def test_vertex_matrix_and_input_lane_mutations_are_detected(self):
        self.assertIn('i8 0, i32 undef)',self.ir)
        self.assertFalse(compare_dataflow(self.ir,self.ir.replace('i8 0, i32 undef)','i8 1, i32 undef)',1))['equal'])
        self.assertFalse(compare_dataflow(self.ir,self.ir.replace('i32 46,','i32 47,',1))['equal'])


VERTEX_TAG='\n!dx.shaderModel = !{!99}\n!99 = !{!"vs", i32 6, i32 6}\n'
BRANCH='''define void @main() {
  %x = call i32 @dx.op.loadInput.i32(i32 4, i32 0, i32 0, i8 0, i32 undef)
  %test = icmp eq i32 %x, 0
  br i1 %test, label %yes, label %no
yes:
YES  br label %end
no:
NO  br label %end
end:
END  ret void
}
'''+VERTEX_TAG
STORE='  call void @dx.op.storeOutput.i32(i32 5, i32 0, i32 0, i8 0, i32 VALUE)\n'


class VertexOutputTests(unittest.TestCase):
    def test_guard_substitution_under_integer_decode_preserves_order_and_values(self):
        original=BRANCH.replace('YES','  %a = and i32 123456, 65535\n  %f = call float @dx.op.legacyF16ToF32(i32 131, i32 %a)\n  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 0, float %f)\n').replace('NO','  %b = and i32 987654, 65535\n  %g = call float @dx.op.legacyF16ToF32(i32 131, i32 %b)\n  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 0, float %g)\n').replace('END','')
        compiled=BRANCH.replace('YES','').replace('NO','').replace('END','''  %p = phi i32 [ 123456, %yes ], [ 987654, %no ]
  %a = and i32 %p, 65535
  %f = call float @dx.op.legacyF16ToF32(i32 131, i32 %a)
  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 0, float %f)
''')
        self.assertTrue(compare_dataflow(original,compiled)['equal'])
        for changed in (compiled.replace('987654','987655'),compiled.replace('65535','32767'),compiled.replace('icmp eq','icmp ne')):
            self.assertFalse(compare_dataflow(original,changed)['equal'])

    def test_disjoint_writes_match_phi_with_exact_branch_values(self):
        original=BRANCH.replace('YES',STORE.replace('VALUE','11')).replace('NO',STORE.replace('VALUE','22')).replace('END','')
        compiled=BRANCH.replace('YES','').replace('NO','').replace('END','  %v = phi i32 [ 11, %yes ], [ 22, %no ]\n'+STORE.replace('VALUE','%v'))
        self.assertTrue(compare_dataflow(original,compiled)['equal'])
        for changed in (compiled.replace('[ 11,','[ 12,'),compiled.replace('icmp eq','icmp ne'),compiled.replace('i8 0, i32 %v','i8 1, i32 %v')):
            self.assertFalse(compare_dataflow(original,changed)['equal'])

    def test_only_source_undefined_output_paths_may_be_refined(self):
        original=BRANCH.replace('YES',STORE.replace('VALUE','11')).replace('NO','').replace('END','')
        refined=BRANCH.replace('YES','').replace('NO','').replace('END',STORE.replace('VALUE','11'))
        self.assertTrue(compare_dataflow(original,refined)['equal'])
        self.assertTrue(compare_dataflow(original,refined)['source_undefined_vertex_outputs_may_be_refined'])
        self.assertFalse(compare_dataflow(refined,original)['equal'])
        for changed in (refined.replace('i32 11)','i32 12)'),refined.replace('i32 11)','i32 undef)')):
            self.assertFalse(compare_dataflow(original,changed)['equal'])

    def test_overlapping_writes_and_pixel_stage_reuse_are_rejected(self):
        original=BRANCH.replace('YES',STORE.replace('VALUE','11')).replace('NO',STORE.replace('VALUE','22')).replace('END','')
        overlapping=original.replace('  ret void',STORE.replace('VALUE','33')+'  ret void')
        with self.assertRaises(ReconstructionError):compare_dataflow(overlapping,overlapping)
        pixel=original.replace('!"vs"','!"ps"')
        with self.assertRaises(ReconstructionError):compare_dataflow(pixel,pixel)


if __name__=='__main__':unittest.main()
