"""Compute metadata and ordered memory preservation, with adversarial mutations."""
import json
from pathlib import Path
import re
import unittest

from mmtr_editor.compute import compute_properties
from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.model import Document
from mmtr_editor.reconstruct import reconstruct_hlsl, ReconstructionError
from mmtr_editor.verify_ir import compare_dataflow

ROOT=Path('output/skin_runtime/stage_inventory')
DLL=Path('C:/Software/Kiro/dxcompiler.dll')

META='''
!dx.shaderModel = !{!1}
!dx.entryPoints = !{!2}
!dx.resources = !{!5}
!1 = !{!"cs", i32 6, i32 2}
!2 = !{void ()* @main, !"main", null, !5, !3}
!3 = !{i32 0, i64 16, i32 4, !4}
!4 = !{i32 256, i32 1, i32 1}
!5 = !{null, !6, null, null}
!6 = !{!7}
!7 = !{i32 0, %struct.RWByteAddressBuffer* undef, !"", i32 0, i32 0, i32 1, i32 11, i1 false, i1 false, i1 false, null}
'''
HANDLE='  %h = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 1, i32 0, i32 0, i1 false)\n'
READ='  %r = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %h, i32 0, i32 undef, i8 1, i32 4)\n'
STORE='  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %h, i32 ADDRESS, i32 undef, i32 VALUE, i32 undef, i32 undef, i32 undef, i8 1, i32 4)\n'


def store(address,value):return STORE.replace('ADDRESS',str(address)).replace('VALUE',str(value))


def program(body):return 'define void @main() {\n'+HANDLE+body+'  ret void\n}\n'+META


class ComputeMemoryTests(unittest.TestCase):
    def test_numthreads_bounds_unknown_flags_and_launch_shape(self):
        original=program(store(0,11))
        self.assertEqual(compute_properties(original),(16,(256,1,1)))
        changed=original.replace('i32 256, i32 1, i32 1','i32 128, i32 1, i32 1')
        self.assertFalse(compare_dataflow(original,changed)['equal'])
        for before,after in [('i64 16','i64 80'),('i32 256, i32 1, i32 1','i32 0, i32 1, i32 1'),
                             ('i32 256, i32 1, i32 1','i32 1024, i32 2, i32 1'),
                             ('i32 256, i32 1, i32 1','i32 1, i32 1, i32 65'),
                             ('i32 4, !4','i32 4, !99')]:
            with self.subTest(after=after),self.assertRaises(ReconstructionError):
                compare_dataflow(original,original.replace(before,after))

    def test_thread_builtin_shapes_and_components(self):
        for name,op,args in [('threadId',93,', i32 0'),('groupId',94,', i32 1'),
                             ('threadIdInGroup',95,', i32 2'),('flattenedThreadIdInGroup',96,'')]:
            call=f'  %t = call i32 @dx.op.{name}.i32(i32 {op}{args})\n'
            original=program(call+store(0,'%t'))
            self.assertTrue(compare_dataflow(original,original)['equal'])
            for bad in [call.replace(f'i32 {op}',f'i32 {op+1}',1),call.replace(name+'.i32',name+'.f32'),
                        call.replace('call i32','call float'),call.replace(f'i32 {op}{args}',f'i32 {op}, i32 3')]:
                with self.subTest(name=name,bad=bad),self.assertRaises(ReconstructionError):
                    compare_dataflow(original,program(bad+store(0,'%t')))
        original=program('  %t = call i32 @dx.op.threadId.i32(i32 93, i32 0)\n'+store(0,'%t'))
        self.assertFalse(compare_dataflow(original,original.replace('i32 93, i32 0','i32 93, i32 1'))['equal'])

    def test_cross_block_write_order_values_addresses_and_guards(self):
        first,second=store(0,11),store(4,22)
        original=program(first+'  br label %next\nnext:\n'+second)
        self.assertTrue(compare_dataflow(original,program(first+second))['equal'])
        for changed in [program(second+first),program(first),original.replace('i32 22,','i32 23,'),
                        original.replace('i32 4, i32 undef','i32 8, i32 undef')]:
            self.assertFalse(compare_dataflow(original,changed)['equal'])
        branch=program('  %t = call i32 @dx.op.threadId.i32(i32 93, i32 0)\n'
                       '  %c = icmp eq i32 %t, 0\n  br i1 %c, label %yes, label %end\nyes:\n'
                       +first+'  br label %end\nend:\n'+second)
        self.assertTrue(compare_dataflow(branch,branch)['equal'])
        self.assertFalse(compare_dataflow(branch,branch.replace('icmp eq','icmp ne'))['equal'])

    def test_uav_reads_are_events_even_when_unused_and_cannot_cross_writes(self):
        extract='  %v = extractvalue %dx.types.ResRet.i32 %r, 0\n'
        original=program(READ+extract+store(4,'%v'))
        self.assertTrue(compare_dataflow(original,original)['equal'])
        self.assertFalse(compare_dataflow(original,original.replace('i32 %v,','i32 42,'))['equal'])
        unused=program(READ+store(4,11))
        self.assertFalse(compare_dataflow(unused,program(store(4,11)))['equal'])
        self.assertFalse(compare_dataflow(unused,unused.replace('i32 0, i32 undef, i8 1','i32 4, i32 undef, i8 1'))['equal'])
        for body in [store(4,11)+READ,READ.replace('i32 139','i32 138')+store(4,11),
                     READ.replace('.i32(','.f32(')+store(4,11),
                     READ.replace('i8 1, i32 4','i8 2, i32 4')+store(4,11)]:
            with self.assertRaises(ReconstructionError):compare_dataflow(unused,program(body))

    def test_unknown_effects_graphics_ops_and_raw_store_shapes_fail_closed(self):
        original=program(store(0,11))
        for extra in ['call void @dx.op.barrier(i32 80, i32 8)',
                      '%a = call i32 @dx.op.atomicBinOp.i32(i32 78)',
                      'call void @dx.op.discard(i32 82, i1 true)',
                      '%a = call i32 @dx.op.loadInput.i32(i32 4, i32 0, i32 0, i8 0, i32 undef)',
                      '%a = call float @dx.op.unary.f32(i32 83, float 0.0)']:
            with self.subTest(extra=extra),self.assertRaises(ReconstructionError):
                compare_dataflow(original,program('  '+extra+'\n'+store(0,11)))
        for before,after in [('i32 140','i32 139'),('i8 1, i32 4','i8 2, i32 4'),
                             ('i8 1, i32 4','i8 1, i32 2'),('i32 0, i32 undef','i32 0, i32 0'),
                             ('i32 11, i32 undef','i32 11, i32 12')]:
            with self.subTest(after=after),self.assertRaises(ReconstructionError):
                compare_dataflow(original,original.replace(before,after))
        loop=program('  br label %loop\nloop:\n'+store(0,11)+'  br label %loop\nend:\n')
        with self.assertRaises(ReconstructionError):compare_dataflow(loop,loop)


@unittest.skipUnless((ROOT/'inventory.json').exists() and DLL.exists(),'Local original skin IR/DXC required')
class RealComputeTests(unittest.TestCase):
    def test_real_static_mesh_compute_original_interfaces_and_memory(self):
        inventory=json.loads((ROOT/'inventory.json').read_text(encoding='utf-8'))
        d=Document.open(inventory['source']);c=Dxc(DLL)
        try:
            rows=json.loads(Path('output/skin_runtime/compute_coverage/support_scan.json').read_text(encoding='utf-8'))['shaders']
            supported=[r['index'] for r in rows if r['status']=='verified_static' and r['entry']=='CS_StaticMeshTransform']
            self.assertEqual(len(supported),24)
            for index in supported:
                item=next(x for x in inventory['entries'] if x['index']==index)
                ir=Path(item['ir']).read_text(encoding='utf-8');r=reconstruct_hlsl(ir)
                self.assertEqual(r['profile'],item['profile'])
                self.assertIn('[numthreads(256, 1, 1)]',r['source'])
                binary,_=c.compile(r['source'],r['entry'],r['profile']);actual=c.disassemble(binary)
                self.assertFalse(d.interface_differences(index,binary))
                self.assertFalse(reflection_differences(ir,actual))
                self.assertTrue(compare_dataflow(ir,actual)['equal'],index)
                if index==8:
                    unrelated=ir+'\n!99999 = !{!"ps", i32 6, i32 6}\n'
                    self.assertEqual(reconstruct_hlsl(unrelated)['source'],r['source'])
                if index==163:
                    bad=ir.replace('i32 11, i32 0 })','i32 12, i32 0 })',1)
                    self.assertNotEqual(ir,bad)
                    with self.assertRaises(ReconstructionError):compare_dataflow(ir,bad)
        finally:c.close()


if __name__=='__main__':unittest.main()
