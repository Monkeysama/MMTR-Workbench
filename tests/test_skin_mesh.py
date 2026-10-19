"""Real acyclic mesh entries and adversarial indexed-output/event checks."""
import json
from pathlib import Path
import re
import unittest

from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.mesh import mesh_interface
from mmtr_editor.model import Document
from mmtr_editor.reconstruct import reconstruct_hlsl, ReconstructionError
from mmtr_editor.verify_ir import compare_dataflow

ROOT=Path('output/skin_runtime/stage_inventory')
DLL=Path('C:/Software/Kiro/dxcompiler.dll')


@unittest.skipUnless((ROOT/'inventory.json').exists() and DLL.exists(),'Original mesh IR/DXC required')
class MeshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv=json.loads((ROOT/'inventory.json').read_text(encoding='utf-8'))
        cls.ir={i:Path(next(r for r in cls.inv['entries'] if r['index']==i)['ir']).read_text(encoding='utf-8') for i in (298,300,310,311)}

    def rejects(self,ir):
        with self.assertRaises(ReconstructionError):compare_dataflow(ir,ir)

    def test_all_four_compile_interface_reflection_flow_and_vector_writes(self):
        doc=Document.open(self.inv['source']);c=Dxc(DLL)
        try:
            for i,ir in self.ir.items():
                with self.subTest(index=i):
                    r=reconstruct_hlsl(ir);s=r['source']
                    self.assertEqual(r['profile'],'ms_6_6')
                    self.assertIn('[outputtopology("triangle")]',s)
                    self.assertIn('out vertices ShaderOutput meshVertices[128]',s)
                    self.assertIn('out indices uint3 meshIndices[128]',s)
                    self.assertIn('out primitives MeshPrimitive meshPrimitives[128]',s)
                    self.assertNotRegex(s,r'meshVertices\[[^\n]+\]\.field_\d+\.[xyzw]\s*=')
                    payload,warnings=c.compile(s,r['entry'],r['profile']);actual=c.disassemble(payload)
                    self.assertFalse(warnings);self.assertFalse(doc.interface_differences(i,payload))
                    self.assertFalse(reflection_differences(ir,actual));self.assertTrue(compare_dataflow(ir,actual)['equal'])
        finally:c.close()

    def test_mesh_properties_unknown_flags_payload_topology_threads_and_bounds(self):
        ir=self.ir[298]
        changes=[('i64 1082130704','i64 1082130705'),('i32 128, i32 128, i32 2, i32 0','i32 128, i32 128, i32 1, i32 0'),
                 ('i32 128, i32 128, i32 2, i32 0','i32 128, i32 128, i32 2, i32 4'),
                 ('i32 128, i32 1, i32 1','i32 129, i32 1, i32 1'),
                 ('i32 128, i32 128, i32 2','i32 257, i32 128, i32 2'),('!"ms", i32 6, i32 6','!"ms", i32 6, i32 5')]
        for a,b in changes:
            with self.subTest(change=b):self.assertIn(a,ir);self.rejects(ir.replace(a,b))

    def test_separate_signatures_types_interpolation_rows_packing_remain_exact(self):
        ir=self.ir[298];iface=mesh_interface(ir)
        self.assertEqual(len(iface['vertices']),2);self.assertEqual(len(iface['primitives']),1)
        for a,b in [('!"MATERIAL", i8 5','!"MATERIAL", i8 9'),
                    ('!"SV_Position", i8 9, i8 3','!"SV_Position", i8 9, i8 0'),
                    ('i8 4, i32 0, i8 0, !24','i8 4, i32 0, i8 1, !24'),
                    ('i8 2, i32 1, i8 0, !26','i8 2, i32 1, i8 3, !26'),
                    ('i8 1, i32 1, i8 1, i32 0','i8 2, i32 1, i8 1, i32 0')]:
            with self.subTest(change=b):self.assertIn(a,ir);self.rejects(ir.replace(a,b))
        changed=ir.replace('i8 2, i32 1, i8 0, !26','i8 2, i32 2, i8 0, !26')
        self.assertFalse(compare_dataflow(ir,changed)['equal'])

    def test_counts_are_single_and_dominate_every_output(self):
        ir=self.ir[298];count=re.search(r'^  call void @dx.op.setMeshOutputCounts.+',ir,re.M)[0]
        for changed in [ir.replace(count,''),ir.replace(count,count+'\n'+count)]:self.rejects(changed)
        # Move the unique count behind every output: same operands, invalid ordering.
        primitive=re.search(r'^  call void @dx.op.storePrimitiveOutput.+',ir,re.M)[0]
        self.rejects(ir.replace(count,'').replace(primitive,primitive+'\n'+count))
        self.rejects(ir.replace(count,'  call void @dx.op.setMeshOutputCounts(i32 168, i32 129, i32 128)'))
        self.assertFalse(compare_dataflow(ir,ir.replace(count,'  call void @dx.op.setMeshOutputCounts(i32 168, i32 0, i32 0)'))['equal'])

    def test_index_vertex_primitive_values_types_rows_lanes_and_bounds(self):
        ir=self.ir[298];line=re.search(r'^  call void @dx.op.storeVertexOutput.f32.+',ir,re.M)[0]
        for bad in [line.replace('i32 171','i32 172'),line.replace('i32 0, i8 0','i32 1, i8 0'),
                    line.replace('i8 0','i8 4'),re.sub(r'float %\d+','i32 0',line),re.sub(r'i32 %\d+\)', 'i32 128)',line)]:
            with self.subTest(bad=bad):self.rejects(ir.replace(line,bad))
        for bad in [re.sub(r'float %\d+','float 1.000000e+00',line),re.sub(r'i32 %\d+\)','i32 0)',line)]:
            self.assertFalse(compare_dataflow(ir,ir.replace(line,bad))['equal'])
        tri=re.search(r'^  call void @dx.op.emitIndices.+',ir,re.M)[0]
        for bad in [tri.replace('i32 169','i32 168'),re.sub(r'i32 %\d+','i32 128',tri,count=1)]:self.rejects(ir.replace(tri,bad))
        self.assertFalse(compare_dataflow(ir,ir.replace(tri,re.sub(r'i32 %\d+','i32 0',tri,count=1)))['equal'])
        prim=re.search(r'^  call void @dx.op.storePrimitiveOutput.i32.+',ir,re.M)[0]
        self.assertFalse(compare_dataflow(ir,ir.replace(prim,re.sub(r'i32 %\d+,','i32 0,',prim)))['equal'])

    def test_output_deletion_duplication_reordering_and_guard_are_observable(self):
        ir=self.ir[298];lines=re.findall(r'^  call void @dx.op.storeVertexOutput.f32.+',ir,re.M);a,b=lines[:2]
        swapped='\n'.join(b if x==a else a if x==b else x for x in ir.splitlines())+'\n'
        variants=[ir.replace(a,''),ir.replace(a,a+'\n'+a),swapped]
        branch=re.search(r'^  br i1 %\d+, label %\d+, label %\d+',ir,re.M)[0]
        variants.append(ir.replace(branch,re.sub(r'br i1 %\d+','br i1 true',branch)))
        for candidate in variants:self.assertFalse(compare_dataflow(ir,candidate)['equal'])

    def test_dynamic_read_descriptor_nonuniform_address_order_and_occurrence(self):
        ir=self.ir[298];heap=re.search(r'^  %\d+ = call %dx.types.Handle @dx.op.createHandleFromHeap.+',ir,re.M)[0]
        rawname=re.match(r'  (%\d+)',heap)[1]
        srv=re.search(r'^  (%\d+) = call %dx.types.Handle @dx.op.annotateHandle\(i32 216, %dx.types.Handle '+rawname+r',',ir,re.M)[1]
        a,b=re.findall(r'^  %\d+ = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32\(i32 139, %dx.types.Handle '+srv+r',.+',ir,re.M)[:2]
        variants=[ir.replace(heap,re.sub(r'i32 %\d+, i1 false','i32 0, i1 false',heap)),
                  ir.replace(heap,heap.replace('i1 true','i1 false')),
                  ir.replace(a,re.sub(r'(Handle '+srv+r', i32 )%\d+',r'\g<1>0',a)),
                  ir.replace(a,re.sub(r'%\d+ =','%extra =',a,count=1)+'\n'+a),
                  '\n'.join(b if x==a else a if x==b else x for x in ir.splitlines())+'\n']
        for j,candidate in enumerate(variants):
            with self.subTest(mutation=j):self.assertNotEqual(candidate,ir);self.assertFalse(compare_dataflow(ir,candidate)['equal'])

    def test_partial_crossed_interrupted_vector_writes_rejected_before_dxc(self):
        ir=self.ir[298];lines=re.findall(r'^  call void @dx.op.storeVertexOutput.f32.+',ir,re.M);a,b=lines[:2]
        variants=[ir.replace(b,''),ir.replace(a,a+'\n  %99999 = add i32 0, 0'),
                  ir.replace(b,b.replace('i32 0, i32 0, i8 1','i32 1, i32 0, i8 1')),
                  '\n'.join(b if x==a else a if x==b else x for x in ir.splitlines())+'\n']
        for candidate in variants:
            with self.assertRaisesRegex(ReconstructionError,'网格'):reconstruct_hlsl(candidate)

    def test_select_phi_half_unpack_equivalence_does_not_hide_condition_or_operand_changes(self):
        ir=self.ir[300];candidate=Path('output/skin_runtime/mesh_coverage/compile_probes/0300_original/compiled.ll').read_text(encoding='utf-8')
        self.assertTrue(compare_dataflow(ir,candidate)['equal'])
        select=re.search(r'^  %\d+ = select i1 %\d+, i32 %\d+, i32 0',candidate,re.M)[0]
        changed=candidate.replace(select,select.replace('i32 0','i32 1'))
        self.assertFalse(compare_dataflow(ir,changed)['equal'])
        masked=candidate.replace(', 65535',', 65534');self.assertNotEqual(masked,candidate)
        self.assertFalse(compare_dataflow(ir,masked)['equal'])
        flipped=candidate.replace(select,re.sub(r'select i1 %\d+','select i1 true',select))
        self.assertFalse(compare_dataflow(ir,flipped)['equal'])


if __name__=='__main__':unittest.main()
