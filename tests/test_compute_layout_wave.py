"""Nested reflected storage, ordered UAV reads and collective execution boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import re
import unittest

from inspect_mmtr import reflection
from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.material_hlsl import MaterialTranslator
from mmtr_editor.model import Document, interface_chunks
from mmtr_editor.reconstruct import reconstruct_hlsl, ReconstructionError
from mmtr_editor.structured_layout import nested_struct_layout
from mmtr_editor.verify_ir import compare_dataflow
from test_skin_compute import program, store, READ

ROOT=Path('output/skin_runtime/stage_inventory')
DLL=Path('C:/Software/Kiro/dxcompiler.dll')


class ComputeCollectiveTests(unittest.TestCase):
    def test_wave_all_true_opcode_type_predicate_and_execution_guard(self):
        wave='  %w = call i1 @dx.op.waveAllTrue(i32 114, i1 true)\n'
        original=program(wave+store(0,11))
        self.assertTrue(compare_dataflow(original,original)['equal'])
        self.assertEqual(compare_dataflow(original,original)['wave_operation_counts'],[1,1])
        self.assertFalse(compare_dataflow(original,original.replace('i1 true)','i1 false)'))['equal'])
        for bad in [wave.replace('i32 114','i32 115'),wave.replace('call i1','call i32'),
                    wave.replace('waveAllTrue(','waveAllTrue.i32('),wave.replace('i1 true','i32 1')]:
            with self.subTest(bad=bad),self.assertRaises(ReconstructionError):
                compare_dataflow(original,program(bad+store(0,11)))
        branch='  %t = call i32 @dx.op.threadId.i32(i32 93, i32 0)\n  %c = icmp eq i32 %t, 0\n'
        original=program(branch+'  br i1 %c, label %yes, label %end\nyes:\n'+wave+'  br label %end\nend:\n'+store(0,11))
        moved=program(branch+wave+'  br i1 %c, label %yes, label %end\nyes:\n  br label %end\nend:\n'+store(0,11))
        self.assertFalse(compare_dataflow(original,moved)['equal'])

    def test_collectives_cannot_cross_uav_reads_writes_or_each_other(self):
        all_true='  %w = call i1 @dx.op.waveAllTrue(i32 114, i1 true)\n'
        first='  %f = call i32 @dx.op.waveReadLaneFirst.i32(i32 118, i32 7)\n'
        for operation in [store(0,11),first,READ]:
            a=program(all_true+operation);b=program(operation+all_true)
            self.assertFalse(compare_dataflow(a,b)['equal'])
        original=program(all_true+store(0,11))
        self.assertFalse(compare_dataflow(original,program(all_true+all_true.replace('%w','%w2')+store(0,11)))['equal'])


SYNTHETIC='''struct Bounds { float3 minpos; float volume; float3 maxpos; uint pad; };
struct State { uint mode; Bounds first; Bounds second; row_major float3x4 matrix; uint padding[3]; };
StructuredBuffer<State> states : register(t0);
StructuredBuffer<uint2> pairs : register(t1);
RWStructuredBuffer<float3> history : register(u0);
RWByteAddressBuffer dest : register(u1);
[numthreads(64,1,1)] void main(uint3 id : SV_DispatchThreadID) {
 float3 x=history[id.x];
 float y=states[id.x].first.minpos.x;
 float z=states[id.x].second.maxpos.z;
 dest.Store4(id.x*16,uint4(asuint(y),asuint(z),pairs[id.x].y,asuint(x.y)));
}'''


@unittest.skipUnless(DLL.exists(),'Local DXC required')
class NestedStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=Dxc(DLL)
        try:
            cls.binary,_=c.compile(SYNTHETIC,'main','cs_6_6');cls.ir=c.disassemble(cls.binary)
        finally:c.close()

    def test_nested_names_vector_elements_and_uav_reads_roundtrip(self):
        r=reconstruct_hlsl(self.ir)
        for text in ['Bounds first;','Bounds second;','StructuredBuffer<uint2> pairs','RWStructuredBuffer<float3> history',
                     'states[v_5].first.minpos.x','states[v_5].second.maxpos.z']:
            self.assertIn(text,r['source'])
        c=Dxc(DLL)
        try:
            binary,_=c.compile(r['source'],r['entry'],r['profile']);actual=c.disassemble(binary)
            self.assertEqual(interface_chunks(self.binary),interface_chunks(binary))
            self.assertFalse(reflection_differences(self.ir,actual))
            self.assertTrue(compare_dataflow(self.ir,actual)['equal'])
        finally:c.close()

    def test_nested_member_identity_is_part_of_reflection_compatibility(self):
        changed=re.sub(r'} first;', '} another;',self.ir,count=1)
        self.assertNotEqual(changed,self.ir)
        self.assertEqual(reflection(self.ir)['structured_buffers'][0]['variables'],reflection(changed)['structured_buffers'][0]['variables'])
        self.assertIn('结构化缓冲反射布局',reflection_differences(self.ir,changed))
        incomplete=re.sub(r';\s+struct struct.Bounds\n','',self.ir,count=1)
        with self.assertRaises(ReconstructionError):reconstruct_hlsl(incomplete)

    def test_nested_offsets_types_stride_and_duplicate_layouts_are_checked(self):
        ref=next(r for r in reflection(self.ir)['structured_buffers'] if r['name']=='states')
        root=re.search(r'^%"hostlayout.class.StructuredBuffer<State>" = type \{ (%[\w.]+) \}',self.ir,re.M)[1]
        declarations,slots=nested_struct_layout(self.ir,'State',root,ref['members'],ref['size'])
        self.assertEqual(len(declarations),2)
        first=next(x for x in ref['members'] if x['name']=='first')
        self.assertEqual(slots[first['offset']//4].expr,'first.minpos.x')
        cases=[]
        changed=deepcopy(ref['members']);changed[1]['offset']+=4;cases.append((self.ir,changed,ref['size']))
        changed=deepcopy(ref['members']);changed[1]['members'][0]['type']='float4';cases.append((self.ir,changed,ref['size']))
        cases.append((self.ir,ref['members'],ref['size']+4))
        changed=self.ir.replace('%struct.Bounds = type { <3 x float>','%struct.Bounds = type { <4 x float>');self.assertNotEqual(changed,self.ir)
        cases.append((changed,ref['members'],ref['size']))
        for ir,members,stride in cases:
            with self.assertRaises(ReconstructionError):nested_struct_layout(ir,'State',root,members,stride)

    def test_structured_uav_read_shape_and_value_mutations_are_detected(self):
        changed=re.sub(r'(rawBufferLoad.f32\(i32 139, %dx.types.Handle %\d+, i32 )%\d+(, i32 0, i8 7)',r'\g<1>0\2',self.ir,count=1)
        self.assertNotEqual(changed,self.ir)
        self.assertFalse(compare_dataflow(self.ir,changed)['equal'])
        for replacement in ['64','2']:
            bad=re.sub(r'(rawBufferLoad.f32\(i32 139, %dx.types.Handle %\d+, i32 %\d+, i32 )0(, i8 7)',r'\g<1>'+replacement+r'\2',self.ir,count=1)
            with self.assertRaises(ReconstructionError):compare_dataflow(self.ir,bad)


@unittest.skipUnless((ROOT/'inventory.json').exists() and DLL.exists(),'Original skin IR/DXC required')
class RealSkinComputeExtensions(unittest.TestCase):
    def test_seven_skinning_entries_keep_wave_guards_and_ordered_events(self):
        inv=json.loads((ROOT/'inventory.json').read_text(encoding='utf-8'));d=Document.open(inv['source']);c=Dxc(DLL)
        try:
            for index in [6,13,15,164,171,331,338]:
                item=next(x for x in inv['entries'] if x['index']==index);ir=Path(item['ir']).read_text(encoding='utf-8')
                r=reconstruct_hlsl(ir);self.assertIn('WaveActiveAllTrue(',r['source'])
                binary,_=c.compile(r['source'],r['entry'],r['profile']);actual=c.disassemble(binary)
                self.assertFalse(d.interface_differences(index,binary))
                self.assertFalse(reflection_differences(ir,actual))
                flow=compare_dataflow(ir,actual);self.assertTrue(flow['equal']);self.assertGreater(flow['wave_operation_counts'][0],0)
                if index==6:
                    mutation=ir.replace('i32 114, i1 %230','i32 114, i1 true',1);self.assertNotEqual(mutation,ir)
                    self.assertFalse(compare_dataflow(ir,mutation)['equal'])
        finally:c.close()

    def test_twelve_cloth_layouts_compile_and_preserve_loop_transitions(self):
        inv=json.loads((ROOT/'inventory.json').read_text(encoding='utf-8'));d=Document.open(inv['source']);c=Dxc(DLL)
        try:
            cloth=[r for r in inv['entries'] if r['stage']=='CS' and r['entry']=='CS_ClothPreTransform']
            self.assertEqual(len(cloth),12)
            for item in cloth:
                ir=Path(item['ir']).read_text(encoding='utf-8');r=reconstruct_hlsl(ir)
                self.assertIn('AABB domainAabb;',r['source']);self.assertIn('AABB boundingAabb;',r['source'])
                binary,_=c.compile(r['source'],r['entry'],r['profile']);actual=c.disassemble(binary)
                self.assertFalse(d.interface_differences(item['index'],binary))
                self.assertFalse(reflection_differences(ir,actual))
                flow=compare_dataflow(ir,actual)
                self.assertTrue(flow['equal'],item['index'])
                self.assertEqual(flow['loop_state_counts'],[4,4])
        finally:c.close()


if __name__=='__main__':unittest.main()
