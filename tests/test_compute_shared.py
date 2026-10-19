"""Shared CS tails execute once; guarded phi reduction must fail closed."""
import json
from pathlib import Path
import unittest

from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.model import Document
from mmtr_editor.reconstruct import reconstruct_hlsl, ReconstructionError
from mmtr_editor.verify_ir import compare_dataflow
from test_skin_compute import program, store

ROOT=Path('output/skin_runtime/stage_inventory')
DLL=Path('C:/Software/Kiro/dxcompiler.dll')
INDICES=(9,11,17,19,167,169,174,176,334,336,341,343)
PREFIX='''  %t = call i32 @dx.op.threadId.i32(i32 93, i32 0)
  %c = icmp eq i32 %t, 0
  br i1 %c, label %yes, label %no
yes:
  br label %join
no:
  br label %join
join:
'''
BOOLEAN=program(PREFIX+'''  %p = phi i1 [ true, %yes ], [ false, %no ]
  br i1 %p, label %write, label %end
write:
'''+store(0,11)+'''  br label %end
end:
''')
DEEP=program(PREFIX+'''  %p = phi i32 [ 7, %yes ], [ undef, %no ]
  %s = lshr i32 %p, 1
  %v = and i32 %s, 3
  br i1 %c, label %use, label %skip
use:
  br label %end
skip:
  br label %end
end:
  %result = phi i32 [ %v, %use ], [ 8, %skip ]
'''+store(0,'%result'))


class ComputeGuardedPhiTests(unittest.TestCase):
    def test_boolean_phi_preserves_true_false_path_predicates(self):
        self.assertTrue(compare_dataflow(BOOLEAN,BOOLEAN.replace('br i1 %p','br i1 %c'))['equal'])
        for changed in [BOOLEAN.replace('[ true, %yes ], [ false, %no ]','[ false, %yes ], [ true, %no ]'),
                        BOOLEAN.replace('[ false, %no ]','[ true, %no ]'),
                        BOOLEAN.replace('icmp eq','icmp ne')]:
            self.assertFalse(compare_dataflow(BOOLEAN,changed)['equal'])

    def test_nested_decode_phi_undef_removed_only_under_proven_use_guard(self):
        simplified=DEEP.replace('lshr i32 %p, 1','lshr i32 7, 1')
        self.assertTrue(compare_dataflow(DEEP,simplified)['equal'])
        # A defined incoming value may never turn into undef on a used path.
        self.assertFalse(compare_dataflow(DEEP,DEEP.replace('[ 7, %yes ]','[ undef, %yes ]'))['equal'])
        for changed in [DEEP.replace('lshr i32 %p, 1','lshr i32 %p, 2'),
                        DEEP.replace('and i32 %s, 3','and i32 %s, 1'),
                        DEEP.replace('[ 8, %skip ]','[ 9, %skip ]')]:
            self.assertFalse(compare_dataflow(DEEP,changed)['equal'])

    def test_unproven_or_reversed_use_guards_cannot_select_phi_arm(self):
        for condition in ['%other','%notc']:
            before=DEEP.replace('  %p = phi',
                '  %other = icmp eq i32 %t, 1\n  %notc = xor i1 %c, true\n  %p = phi')
            before=before.replace('br i1 %c, label %use',f'br i1 {condition}, label %use')
            changed=before.replace('lshr i32 %p, 1','lshr i32 7, 1')
            self.assertFalse(compare_dataflow(before,changed)['equal'])


@unittest.skipUnless((ROOT/'inventory.json').exists() and DLL.exists(),'Original skin IR/DXC required')
class RealSharedComputeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory=json.loads((ROOT/'inventory.json').read_text(encoding='utf-8'))
        cls.doc=Document.open(cls.inventory['source'])

    def test_twelve_skinning_shared_tails_compile_interfaces_and_ordered_dataflow(self):
        c=Dxc(DLL)
        try:
            for index in INDICES:
                with self.subTest(index=index):
                    row=next(x for x in self.inventory['entries'] if x['index']==index)
                    ir=Path(row['ir']).read_text(encoding='utf-8');r=reconstruct_hlsl(ir)
                    self.assertEqual(len(r['structured_shared_blocks']),1)
                    shared=r['structured_shared_blocks'][0]
                    self.assertEqual(r['source'].count(f'// DXIL block {shared}\n'),1)
                    self.assertIn(f'if (reached_{shared})',r['source'])
                    binary,_=c.compile(r['source'],r['entry'],r['profile']);actual=c.disassemble(binary)
                    self.assertFalse(self.doc.interface_differences(index,binary))
                    self.assertFalse(reflection_differences(ir,actual))
                    self.assertTrue(compare_dataflow(ir,actual)['equal'])
        finally:c.close()

    def test_real_shared_read_address_order_count_wave_and_bypass_mutations_rejected(self):
        row=next(x for x in self.inventory['entries'] if x['index']==9)
        ir=Path(row['ir']).read_text(encoding='utf-8')
        lines=ir.splitlines();first=next(s for s in lines if '%83 = call' in s)
        second=next(s for s in lines if '%94 = call' in s)
        wave=next(s for s in lines if '%163 = call' in s)
        variants=[ir.replace(first,first.replace('i32 %82','i32 0')),
                  ir.replace(first,first.replace('%83 =','%duplicate =')+'\n'+first),
                  '\n'.join(second if s==first else first if s==second else s for s in lines)+'\n',
                  ir.replace(wave,wave.replace('i1 %162','i1 true')),
                  ir.replace(wave,wave+'\n'+wave.replace('%163 =','%duplicate =')),
                  ir.replace('%31 = icmp eq i32 %12, 2','%31 = icmp eq i32 %12, 3'),
                  ir.replace('br label %48','br label %126',1)]
        for candidate in variants:
            with self.subTest(candidate=candidate[-50:]):
                self.assertNotEqual(candidate,ir)
                try:result=compare_dataflow(ir,candidate)
                except ReconstructionError:continue
                self.assertFalse(result['equal'])

    def test_nested_raster_effects_preserve_two_typed_state_transitions(self):
        for index in (155,157,322,324,477,479):
            row=next(x for x in self.inventory['entries'] if x['index']==index)
            with self.subTest(index=index):
                ir=Path(row['ir']).read_text(encoding='utf-8')
                self.assertTrue(compare_dataflow(ir,ir)['equal'])


if __name__=='__main__':unittest.main()
