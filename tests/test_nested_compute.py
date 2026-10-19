"""Research nested-loop induction and ordered side-effect counterexamples."""
import json
from pathlib import Path
import re
import unittest
from mmtr_editor.nested_compute import compare_nested_compute_dataflow
from mmtr_editor.reconstruct import ReconstructionError
from mmtr_editor.verify_ir import Graph,compare_dataflow
from test_skin_compute import META,HANDLE,store


NESTED='''define void @main() {
HANDLE  %t = call i32 @dx.op.threadId.i32(i32 93, i32 0)
  %p = getelementptr [4 x float], [4 x float] addrspace(3)* @cache, i32 0, i32 0
  store float 1.000000e+00, float addrspace(3)* %p, align 4
  call void @dx.op.barrier(i32 80, i32 9)
  %x = load float, float addrspace(3)* %p, align 4
  br label %outer
outer:
  %i = phi i32 [ 0, %0 ], [ %nexti, %olatch ]
  %total = phi i32 [ 0, %0 ], [ %nexttotal, %olatch ]
  br label %innerpre
innerpre:
  br label %inner
inner:
  %j = phi i32 [ %i, %innerpre ], [ %nextj, %ilatch ]
  %sum = phi i32 [ %total, %innerpre ], [ %nextsum, %ilatch ]
  %late = icmp eq i32 %j, 2
  br i1 %late, label %yes, label %no
yes:
  %v1 = add i32 %sum, 7
  br label %ilatch
no:
  %v2 = add i32 %sum, 7
  br label %ilatch
ilatch:
  %nextsum = phi i32 [ %v1, %yes ], [ %v2, %no ]
  %a = call i32 @dx.op.atomicBinOp.i32(i32 78, %dx.types.Handle %h, i32 0, i32 %j, i32 undef, i32 undef, i32 %nextsum)
  %nextj = add i32 %j, 1
  %innercont = icmp ult i32 %nextj, 4
  br i1 %innercont, label %inner, label %olatch
olatch:
  %nexti = add i32 %i, 1
  %nexttotal = add i32 %nextsum, 3
  %outercont = icmp ult i32 %nexti, 4
  br i1 %outercont, label %outer, label %end
end:
STORE  ret void
}
'''.replace('HANDLE',HANDLE).replace('STORE',store(16,'%nexttotal'))+META+'\n@cache = addrspace(3) global [4 x float] undef, align 4\n'


class NestedComputeTests(unittest.TestCase):
    def changed(self,candidate):
        self.assertNotEqual(NESTED,candidate)
        self.assertFalse(compare_nested_compute_dataflow(NESTED,candidate)['equal'])

    def test_two_level_self_comparison_and_both_typed_bijections(self):
        result=compare_nested_compute_dataflow(NESTED,NESTED)
        self.assertTrue(result['equal']);self.assertEqual(result['loop_state_counts'],[[2,2],[2,2]])
        changed=NESTED
        for a,b in [('  %i = phi i32 [ 0, %0 ], [ %nexti, %olatch ]\n','  %total = phi i32 [ 0, %0 ], [ %nexttotal, %olatch ]\n'),
                    ('  %j = phi i32 [ %i, %innerpre ], [ %nextj, %ilatch ]\n','  %sum = phi i32 [ %total, %innerpre ], [ %nextsum, %ilatch ]\n')]:
            changed=changed.replace(a+b,b+a)
        changed=re.sub(r'%nextsum\b','%renamed',changed)
        self.assertNotEqual(changed,NESTED)
        report=compare_nested_compute_dataflow(NESTED,changed)
        self.assertTrue(report['equal']);self.assertGreater(report['state_bijections_checked'],2)

    def test_initial_feedback_continuation_and_late_iteration_changes(self):
        for a,b in [('phi i32 [ 0, %0 ]','phi i32 [ 1, %0 ]'),
                    ('phi i32 [ %i, %innerpre ]','phi i32 [ 1, %innerpre ]'),
                    ('add i32 %i, 1','add i32 %i, 2'),('add i32 %j, 1','add i32 %j, 2'),
                    ('ult i32 %nextj, 4','ult i32 %nextj, 5'),('ult i32 %nexti, 4','ult i32 %nexti, 5'),
                    ('%v1 = add i32 %sum, 7','%v1 = add i32 %sum, 9'),
                    ('add i32 %nextsum, 3','add i32 %nextsum, 4')]:
            with self.subTest(after=b):self.changed(NESTED.replace(a,b))

    def test_shared_memory_and_barrier_events_cannot_disappear_or_move(self):
        barrier='  call void @dx.op.barrier(i32 80, i32 9)\n'
        load='  %x = load float, float addrspace(3)* %p, align 4\n'
        for candidate in [NESTED.replace(barrier,''),NESTED.replace(load,''),NESTED.replace(barrier,barrier+barrier),
                          NESTED.replace(load,load+load.replace('%x =','%unused =')),
                          NESTED.replace(barrier,'').replace('innerpre:\n','innerpre:\n'+barrier),
                          NESTED.replace('store float 1.000000e+00','store float 2.000000e+00')]:
            with self.subTest(candidate=candidate):self.changed(candidate)

    def test_atomics_preserve_per_iteration_order_occurrence_address_and_result(self):
        atomic=next(line+'\n' for line in NESTED.splitlines() if '%a = call' in line)
        for candidate in [NESTED.replace(atomic,''),NESTED.replace(atomic,atomic+atomic.replace('%a =','%unused =')),
                          NESTED.replace(atomic,'').replace('olatch:\n','olatch:\n'+atomic),
                          NESTED.replace('i32 %j, i32 undef','i32 %i, i32 undef')]:self.changed(candidate)
        # Equal atomic shapes still return distinct external observations.
        pair=atomic+atomic.replace('%a =','%b =')
        a=NESTED.replace(atomic,pair).replace('add i32 %j, 1','add i32 %a, 1')
        b=a.replace('add i32 %a, 1','add i32 %b, 1')
        self.assertTrue(compare_nested_compute_dataflow(a,a)['equal'])
        self.assertFalse(compare_nested_compute_dataflow(a,b)['equal'])

    def test_event_prefix_is_independent_of_mutually_exclusive_block_layout(self):
        read='  %z = load float, float addrspace(3)* %p, align 4\n'
        a=NESTED.replace('  %v1 =',read+'  %v1 =').replace('  %v2 =',read.replace('%z =','%zz =')+'  %v2 =')
        start=a.index('yes:\n');end=a.index('ilatch:\n');yes,no=a[start:end].split('no:\n')
        b=a[:start]+'no:\n'+no+yes+a[end:]
        self.assertNotEqual(a,b)
        self.assertTrue(compare_nested_compute_dataflow(a,b)['equal'])

    def test_bounds_bad_state_edges_and_general_shapes_fail_closed(self):
        with self.assertRaises(ReconstructionError):compare_nested_compute_dataflow(NESTED,NESTED,max_bijections=0)
        for candidate in [NESTED.replace('[ %nextj, %ilatch ]','[ %nextj, %yes ]'),
                          NESTED.replace('[ %i, %innerpre ]','[ %j, %innerpre ]'),
                          NESTED.replace('br label %innerpre','br label %end'),
                          NESTED.replace('i32 80, i32 9','i32 80, i32 8')]:
            with self.assertRaises(ReconstructionError):compare_nested_compute_dataflow(NESTED,candidate)
        self.assertTrue(compare_dataflow(NESTED,NESTED)['equal'])

    def test_counter_ult_ne_only_after_entry_and_unit_step_are_proved(self):
        # Establish both base cases explicitly; the inner bound is invariant
        # during an inner iteration even though its initial value is outer i.
        a=NESTED.replace('  br label %outer','  %outerentry = icmp ult i32 0, 4\n  br i1 %outerentry, label %outer, label %end',1)
        a=a.replace('  br label %innerpre','  %innerentry = icmp ult i32 %i, 4\n  br i1 %innerentry, label %innerpre, label %olatch',1)
        # The bypass must define the same suffix values. Use only j for the
        # outer feedback so this fixture does not need bypass live-out phis.
        a=a.replace('add i32 %nextsum, 3','add i32 %total, 3')
        a=a.replace('i32 %nexttotal,','i32 %t,')
        b=a.replace('icmp ult i32 %nextj, 4','icmp ne i32 %nextj, 4').replace('icmp ult i32 %nexti, 4','icmp ne i32 %nexti, 4')
        self.assertTrue(compare_nested_compute_dataflow(a,b)['equal'])
        self.assertEqual(len(Graph(a,nested_compute=True).nested_counter_invariants),2)
        # Without the proven base, a uint can exceed the bound or wrap. Unit
        # step and unsigned comparison are necessary, not optimizer flags.
        no_base=NESTED.replace('icmp ult i32 %nextj, 4','icmp ne i32 %nextj, 4')
        self.assertFalse(compare_nested_compute_dataflow(NESTED,no_base)['equal'])
        for before,after in [('add i32 %j, 1','add i32 %j, 2'),
                             ('icmp ult i32 %i, 4','icmp ult i32 %i, 5'),
                             ('icmp ult i32 0, 4','icmp ult i32 0, 5'),
                             ('icmp ult i32 %i, 4','icmp slt i32 %i, 4')]:
            left=a.replace(before,after);right=b.replace(before,after)
            self.assertNotEqual(left,a)
            self.assertFalse(compare_nested_compute_dataflow(left,right)['equal'])

    def test_signed_shift_rewrite_only_when_sign_bits_are_discarded(self):
        arithmetic='  %shift = ashr i32 %i, 2\n  %plus = add i32 %shift, %sum\n  %address = shl i32 %plus, 4\n'
        a=NESTED.replace('  %a = call',arithmetic+'  %a = call').replace('i32 %j, i32 undef','i32 %address, i32 undef')
        b=a.replace('ashr i32 %i, 2','lshr i32 %i, 2')
        self.assertTrue(compare_nested_compute_dataflow(a,b)['equal'])
        # With only one high bit discarded, sign fill can still affect the
        # address. Also reject a changed shift or a consumed full-width value.
        self.assertFalse(compare_nested_compute_dataflow(a.replace('i32 %plus, 4','i32 %plus, 1'),b.replace('i32 %plus, 4','i32 %plus, 1'))['equal'])
        self.assertFalse(compare_nested_compute_dataflow(a,b.replace('lshr i32 %i, 2','lshr i32 %i, 3'))['equal'])
        self.assertFalse(compare_nested_compute_dataflow(a.replace('i32 %address, i32 undef','i32 %shift, i32 undef'),b.replace('i32 %address, i32 undef','i32 %shift, i32 undef'))['equal'])

    def test_full_history_bounds_reject_instead_of_dropping_paths(self):
        body=''
        for i in range(10):
            body+=f'  %bit{i} = and i32 %t, {1<<i}\n  %c{i} = icmp eq i32 %bit{i}, 0\n'
            body+=f'  br i1 %c{i}, label %y{i}, label %n{i}\ny{i}:\n'+store(i*4,11)
            body+=f'  br label %q{i}\nn{i}:\n'+store(i*4,22)+f'  br label %q{i}\nq{i}:\n'
        large=NESTED.replace('  %late =',body+'  %late =')
        with self.assertRaisesRegex(ReconstructionError,'512'):compare_nested_compute_dataflow(large,large)
        loads=''.join(f'  %unused{i} = load float, float addrspace(3)* %p, align 4\n' for i in range(129))
        large=NESTED.replace('  %late =',loads+'  %late =')
        with self.assertRaisesRegex(ReconstructionError,'128|受限模型'):compare_nested_compute_dataflow(large,large)

    def test_collectives_inside_nested_iteration_keep_order_count_and_guard(self):
        first='  %w1 = call i32 @dx.op.waveActiveBit.i32(i32 120, i32 %sum, i8 1)\n'
        second='  %w2 = call i32 @dx.op.wavePrefixOp(i32 136, i1 %late)\n'
        a=NESTED.replace('  br i1 %late,',first+second+'  br i1 %late,')
        self.assertTrue(compare_dataflow(a,a)['equal'])
        for b in [a.replace(first,''),a.replace(first,first+first.replace('%w1 =','%duplicate =')),
                  a.replace(first+second,second+first),a.replace(first,'').replace('yes:\n','yes:\n'+first),
                  a.replace('i32 136, i1 %late','i32 136, i1 true')]:
            self.assertNotEqual(a,b);self.assertFalse(compare_dataflow(a,b)['equal'])

    def test_shared_reads_and_writes_inside_loop_are_occurrence_bearing(self):
        load='  %iteration = load float, float addrspace(3)* %p, align 4\n  %bits = bitcast float %iteration to i32\n'
        write='  store float 2.000000e+00, float addrspace(3)* %p, align 4\n'
        a=NESTED.replace('  %late =',load+'  %late =').replace('%v1 = add i32 %sum, 7','%v1 = add i32 %sum, %bits')
        a=a.replace('  %nextj =',write+'  %nextj =')
        self.assertTrue(compare_dataflow(a,a)['equal'])
        for b in [a.replace(load,'').replace('  br label %outer',load+'  br label %outer',1),
                  a.replace(write,''),a.replace(write,'').replace('  %late =',write+'  %late ='),
                  a.replace(load,load+load.splitlines()[0].replace('%iteration =','%unused =')+'\n')]:
            self.assertNotEqual(a,b);self.assertFalse(compare_dataflow(a,b)['equal'])


ROOT=Path('output/skin_runtime')


@unittest.skipUnless((ROOT/'stage_inventory/inventory.json').exists(),'Original raster evidence required')
class RealNestedComputeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        inv=json.loads((ROOT/'stage_inventory/inventory.json').read_text(encoding='utf-8'))
        cls.entries=[r for r in inv['entries'] if r['entry']=='MeshletSoftwareRasterizer']

    def test_all_twelve_recompiled_entries_match_both_transitions_and_effects(self):
        for item in self.entries:
            with self.subTest(index=item['index']):
                a=Path(item['ir']).read_text(encoding='utf-8')
                b=(ROOT/f'compute_coverage/raster_reconstruction/{item["index"]:04d}/recompiled.ll').read_text(encoding='utf-8')
                r=compare_nested_compute_dataflow(a,b)
                self.assertTrue(r['equal']);self.assertEqual(len(r['state_mapping']),10 if item['index'] in (155,156,322,323,477,478) else 12)

    def test_real_shared_atomic_descriptor_and_loop_guard_mutations(self):
        for index in (155,157):
            item=next(r for r in self.entries if r['index']==index);a=Path(item['ir']).read_text(encoding='utf-8')
            atomic=next(line for line in a.splitlines() if '@dx.op.atomicBinOp.' in line and line.startswith('  %'))
            shared=next(line for line in a.splitlines() if ' = load float, float addrspace(3)*' in line)
            barrier=next(line for line in a.splitlines() if 'call void @dx.op.barrier' in line)
            variants=[a.replace(atomic,atomic.replace('i32 7,','i32 6,',1)),
                      a.replace(atomic,atomic+'\n'+re.sub(r'%\d+ =','%duplicate =',atomic,count=1)),
                      a.replace(shared,shared+'\n'+re.sub(r'%\d+ =','%unused =',shared,count=1)),
                      a.replace(barrier,barrier+'\n'+barrier),
                      a.replace('i32 -1, i32 1, i8 0','i32 -1, i32 2, i8 0'),
                      re.sub(r'(add i32 %\d+, )1\n',r'\g<1>2\n',a,count=1)]
            for candidate in variants:
                with self.subTest(index=index):
                    self.assertNotEqual(candidate,a)
                    try:r=compare_nested_compute_dataflow(a,candidate)
                    except ReconstructionError:continue
                    self.assertFalse(r['equal'])


if __name__=='__main__':unittest.main()
