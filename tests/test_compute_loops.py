"""Arbitrary-state compute transitions, ordered per-iteration reads and negatives."""
import json
from pathlib import Path
import re
import unittest

from mmtr_editor.reconstruct import ReconstructionError
from mmtr_editor.verify_ir import compare_dataflow,Graph
from test_skin_compute import META,HANDLE,store,program


LOOP='''define void @main() {
HANDLE  %t = call i32 @dx.op.threadId.i32(i32 93, i32 0)
  %enter = icmp ult i32 %t, 256
  br i1 %enter, label %loop, label %end
loop:
  %sum = phi float [ 0.000000e+00, %0 ], [ %next, %latch ]
  %i = phi i32 [ 0, %0 ], [ %inc, %latch ]
READ  %lane = extractvalue %dx.types.ResRet.i32 %r, 0
  %f = uitofp i32 %lane to float
  %late = icmp eq i32 %i, 2
  br i1 %late, label %yes, label %no
yes:
  %a = fadd float %sum, %f
  br label %latch
no:
  %b = fadd float %sum, %f
  br label %latch
latch:
  %next = phi float [ %a, %yes ], [ %b, %no ]
  %inc = add i32 %i, 1
  %cont = icmp ult i32 %inc, 4
  br i1 %cont, label %loop, label %exit
exit:
  %bits = bitcast float %next to i32
STORE  br label %end
end:
  ret void
}
'''.replace('HANDLE',HANDLE).replace('READ','  %r = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %h, i32 0, i32 undef, i8 1, i32 4)\n').replace('STORE',store(16,'%bits'))+META


class ComputeLoopTests(unittest.TestCase):
    def assert_changed(self,changed):
        self.assertNotEqual(LOOP,changed)
        self.assertFalse(compare_dataflow(LOOP,changed)['equal'])

    def test_header_order_ssa_renaming_and_typed_state_bijection(self):
        report=compare_dataflow(LOOP,LOOP)
        self.assertTrue(report['equal']);self.assertEqual(report['loop_state_counts'],[2,2])
        first='  %sum = phi float [ 0.000000e+00, %0 ], [ %next, %latch ]\n'
        second='  %i = phi i32 [ 0, %0 ], [ %inc, %latch ]\n'
        reordered=LOOP.replace(first+second,second+first)
        self.assertNotEqual(reordered,LOOP)
        reordered=re.sub(r'%sum\b','%renamed',reordered)
        self.assertTrue(compare_dataflow(LOOP,reordered)['equal'])

    def test_initial_feedback_increment_and_entry_exit_changes_are_detected(self):
        for before,after in [('phi float [ 0.000000e+00','phi float [ 1.000000e+00'),
                             ('phi i32 [ 0,','phi i32 [ 1,'),('add i32 %i, 1','add i32 %i, 2'),
                             ('ult i32 %inc, 4','ult i32 %inc, 5'),('ult i32 %t, 256','ult i32 %t, 128'),
                             ('%b = fadd float %sum, %f','%b = fsub float %sum, %f'),
                             ('%next, %latch','%f, %latch')]:
            with self.subTest(after=after):self.assert_changed(LOOP.replace(before,after))

    def test_difference_only_on_a_later_iteration_is_detected(self):
        # First iteration sees i=0. A first-iteration-only comparison would miss this.
        self.assert_changed(LOOP.replace('%a = fadd float %sum, %f','%a = fadd float %sum, 1.000000e+00'))

    def test_loop_read_address_occurrence_and_phase_are_observable(self):
        read=next(line+'\n' for line in LOOP.splitlines() if '%r = call' in line)
        self.assert_changed(LOOP.replace('i32 0, i32 undef, i8 1','i32 4, i32 undef, i8 1'))
        duplicate=read.replace('%r =','%unused =')
        self.assert_changed(LOOP.replace(read,read+duplicate))
        outside=LOOP.replace(read,'').replace('  %enter =',read+'  %enter =')
        self.assert_changed(outside)
        with self.assertRaises(ReconstructionError):compare_dataflow(LOOP,LOOP.replace('i8 1, i32 4','i8 2, i32 4'))

    def test_memory_writes_collectives_and_synchronization_inside_or_before_fail_closed(self):
        for extra in [store(0,11),'  %w = call i1 @dx.op.waveAllTrue(i32 114, i1 true)\n',
                      '  call void @dx.op.barrier(i32 80, i32 9)\n',
                      '  %a0 = call i32 @dx.op.atomicBinOp.i32(i32 78)\n']:
            bad=LOOP.replace('  %late =',extra+'  %late =')
            with self.subTest(extra=extra),self.assertRaises(ReconstructionError):compare_dataflow(LOOP,bad)
        bad=LOOP.replace('  %enter =',store(0,11)+'  %enter =')
        with self.assertRaises(ReconstructionError):compare_dataflow(LOOP,bad)

    def test_suffix_write_changes_and_removing_the_loop_are_detected(self):
        self.assert_changed(LOOP.replace('i32 16, i32 undef','i32 20, i32 undef'))
        self.assert_changed(LOOP.replace('i32 %bits,','i32 0,'))
        self.assertFalse(compare_dataflow(LOOP,program(store(16,0)))['equal'])
        with self.assertRaises(ReconstructionError):compare_dataflow(LOOP,LOOP.replace('i32 139','i32 138'))

    def test_bounded_event_expansion_rejects_excessive_sequences(self):
        oversized=program(''.join(store(i*4,i) for i in range(129)))
        with self.assertRaisesRegex(ReconstructionError,'受限模型'):compare_dataflow(oversized,oversized)
        body='  %t = call i32 @dx.op.threadId.i32(i32 93, i32 0)\n'
        for i in range(10):
            body+=f'  %bit{i} = and i32 %t, {1<<i}\n  %c{i} = icmp eq i32 %bit{i}, 0\n'
            body+=f'  br i1 %c{i}, label %yes{i}, label %no{i}\nyes{i}:\n'+store(i*4,11)
            body+=f'  br label %join{i}\nno{i}:\n'+store(i*4,22)+f'  br label %join{i}\njoin{i}:\n'
        paths=program(body)
        with self.assertRaisesRegex(ReconstructionError,'512'):compare_dataflow(paths,paths)


ROOT=Path('output/skin_runtime/stage_inventory')


@unittest.skipUnless((ROOT/'0152_cs_CS_ClothPreTransform.ll').exists(),'Original cloth IR required')
class RealClothLoopMutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.ir=(ROOT/'0152_cs_CS_ClothPreTransform.ll').read_text(encoding='utf-8')

    def test_real_count_initial_accumulator_and_exit_mutations(self):
        for before,after in [('%213, 1','%213, 2'),('i32 %300, %205','i32 %300, %206'),
                             ('float [ %297, %296 ], [ 0.000000e+00','float [ %297, %296 ], [ 1.000000e+00'),
                             ('fadd fast float %290, %210','fsub fast float %290, %210')]:
            changed=self.ir.replace(before,after,1);self.assertNotEqual(changed,self.ir)
            with self.subTest(after=after):self.assertFalse(compare_dataflow(self.ir,changed)['equal'])

    def test_real_uav_read_order_and_loop_srv_mask_are_checked(self):
        a=next(line for line in self.ir.splitlines() if '%242 = call' in line)
        b=next(line for line in self.ir.splitlines() if '%246 = call' in line)
        changed=self.ir.replace(a,'PLACEHOLDER').replace(b,a).replace('PLACEHOLDER',b)
        self.assertFalse(compare_dataflow(self.ir,changed)['equal'])
        changed=self.ir.replace('i32 %214, i32 0, i8 3, i32 4','i32 %214, i32 0, i8 2, i32 4')
        self.assertNotEqual(changed,self.ir)
        with self.assertRaises(ReconstructionError):compare_dataflow(self.ir,changed)


if __name__=='__main__':unittest.main()
