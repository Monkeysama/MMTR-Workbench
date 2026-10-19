"""Shared-memory events, typed atomics and nested source emission boundaries."""
from collections import Counter
import json
from pathlib import Path
import re
import unittest

from mmtr_editor.control_flow import natural_loop_forest
from mmtr_editor.dxc import Dxc,reflection_differences
from mmtr_editor.model import interface_chunks,Document
from mmtr_editor.reconstruct import reconstruct_hlsl,ReconstructionError
from mmtr_editor.verify_ir import compare_dataflow
from mmtr_editor.shared_memory import analyze_shared_memory

DLL=Path('C:/Software/Kiro/dxcompiler.dll')
ROOT=Path('output/skin_runtime/stage_inventory')
SHARED='''groupshared float cache[384]; RWByteAddressBuffer dest:register(u0);
[numthreads(128,1,1)]void main(uint id:SV_GroupIndex){
cache[id*3]=float(id);cache[id*3+1]=1;cache[id*3+2]=2;
GroupMemoryBarrierWithGroupSync();float x=cache[(id^1)*3];dest.Store(id*4,asuint(x));}'''
WIDE='''RWStructuredBuffer<uint64_t> dest:register(u0);
[numthreads(128,1,1)]void main(uint id:SV_GroupIndex){
uint64_t v=(uint64_t(id)<<32)|uint64_t(id+1);uint64_t a,b;
InterlockedMax(dest[id],v,a);InterlockedMax(dest[id+1],v|uint64_t(7),b);}'''
TEXTURE='''RWTexture2D<uint> dest:register(u0);
[numthreads(128,1,1)]void main(uint id:SV_GroupIndex){uint old;InterlockedMax(dest[uint2(id,1)],id,old);}'''
WAVE='''RWByteAddressBuffer dest:register(u0);
[numthreads(128,1,1)]void main(uint id:SV_GroupIndex){uint a=WaveActiveBitOr(id);
uint b=WavePrefixCountBits(id>3);dest.Store(id*4,a+b);}'''


class LoopForestTests(unittest.TestCase):
    def test_two_nested_loops_classify_parent_entry_latch_and_exit(self):
        blocks={'0':['br label %outer'],'outer':['br i1 %x, label %innerpre, label %outerlatch'],
                'innerpre':['br label %inner'],'inner':['br i1 %y, label %inner, label %outerlatch'],
                'outerlatch':['br i1 %z, label %outer, label %end'],'end':['ret void']}
        loops=natural_loop_forest(blocks)
        self.assertEqual([(l['header'],l['parent']) for l in loops],[('outer',None),('inner','outer')])
        self.assertEqual(loops[1]['preheader'],'innerpre');self.assertEqual(loops[1]['exit'],'outerlatch')
        bad=dict(blocks);bad['inner']=['br i1 %y, label %inner, label %end']
        with self.assertRaises(ReconstructionError):natural_loop_forest(bad)
        bad=dict(blocks);bad['0']=['br i1 %x, label %outer, label %inner']
        with self.assertRaises(ReconstructionError):natural_loop_forest(bad)


@unittest.skipUnless(DLL.exists(),'Local DXC required')
class SharedMemoryEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c=Dxc(DLL)
        try:
            cls.fixtures={}
            for key,source in [('shared',SHARED),('wide',WIDE),('texture',TEXTURE),('wave',WAVE)]:
                b,_=c.compile(source,'main','cs_6_6');cls.fixtures[key]=(b,c.disassemble(b))
        finally:c.close()

    def test_shared_float_events_reconstruct_compile_and_preserve_occurrences(self):
        b,ir=self.fixtures['shared'];r=reconstruct_hlsl(ir);c=Dxc(DLL)
        try:actual,_=c.compile(r['source'],r['entry'],r['profile']);compiled=c.disassemble(actual)
        finally:c.close()
        self.assertEqual(interface_chunks(b),interface_chunks(actual));self.assertFalse(reflection_differences(ir,compiled))
        self.assertTrue(compare_dataflow(ir,compiled)['equal'])
        self.assertIn('groupshared float shared_float_0[384]',r['source'])
        self.assertEqual(r['source'].count('GroupMemoryBarrierWithGroupSync();'),1)

    def test_shared_store_load_barrier_order_address_value_and_unused_reads_detected(self):
        _,ir=self.fixtures['shared']
        stores=re.findall(r'^  store float.+',ir,re.M);load=re.search(r'^  %\d+ = load float, float addrspace\(3\).+',ir,re.M)[0]
        barrier=re.search(r'^  call void @dx.op.barrier.+',ir,re.M)[0]
        pointer=re.search(r'^  %\d+ = getelementptr .+\n'+re.escape(load),ir,re.M)[0].splitlines()[0]
        variants=[ir.replace(stores[0]+'\n',''),ir.replace(stores[1],stores[1].replace('1.000000e+00','3.000000e+00')),
                  ir.replace(barrier,barrier+'\n'+barrier),ir.replace(barrier+'\n',''),
                  ir.replace(barrier+'\n','').replace(stores[0],barrier+'\n'+stores[0]),
                  ir.replace(load,re.sub(r'%\d+ =','%unused =',load,count=1)+'\n'+load),
                  ir.replace(pointer,re.sub(r'i32 %\d+$','i32 0',pointer))]
        for candidate in variants:
            self.assertNotEqual(candidate,ir);self.assertFalse(compare_dataflow(ir,candidate)['equal'])
        # Executed load cannot disappear just because its scalar result is unused.
        before=ir.replace(load,re.sub(r'%\d+ =','%unused =',load,count=1)+'\n'+load)
        self.assertFalse(compare_dataflow(before,ir)['equal'])

    def test_shared_and_barrier_execution_guards_are_observable(self):
        from test_skin_compute import program
        branch='''  %t = call i32 @dx.op.threadId.i32(i32 93, i32 0)
  %c = icmp eq i32 %t, 0
  br i1 %c, label %yes, label %end
yes:
  call void @dx.op.barrier(i32 80, i32 9)
  br label %end
end:
'''
        a=program(branch);self.assertTrue(compare_dataflow(a,a)['equal'])
        self.assertFalse(compare_dataflow(a,a.replace('icmp eq','icmp ne'))['equal'])
        moved=a.replace('  call void @dx.op.barrier(i32 80, i32 9)\n','').replace('end:\n','end:\n  call void @dx.op.barrier(i32 80, i32 9)\n')
        self.assertFalse(compare_dataflow(a,moved)['equal'])

    def test_consumed_atomic_results_keep_distinct_occurrence_identity(self):
        from test_skin_compute import program,store
        first='  %a = call i32 @dx.op.atomicBinOp.i32(i32 78, %dx.types.Handle %h, i32 0, i32 0, i32 undef, i32 undef, i32 1)\n'
        second=first.replace('%a =','%b =')
        a=program(first+second+store(4,'%a'))
        self.assertTrue(compare_dataflow(a,a)['equal'])
        self.assertFalse(compare_dataflow(a,a.replace('i32 %a,','i32 %b,'))['equal'])

    def test_unknown_shared_types_alignments_barrier_modes_and_escape_rejected(self):
        _,ir=self.fixtures['shared']
        for candidate in [ir.replace('[384 x float]','[384 x i32]'),ir.replace('addrspace(3)*','addrspace(3)*',1).replace('align 4','align 8'),
                          ir.replace('i32 80, i32 9','i32 80, i32 8'),ir.replace('i32 80, i32 9','i32 80, i32 11')]:
            self.assertNotEqual(candidate,ir)
            with self.assertRaises(ReconstructionError):compare_dataflow(ir,candidate)
        arrays='@cache = addrspace(3) global [4 x float] undef, align 4\n'
        with self.assertRaisesRegex(ReconstructionError,'逃逸'):
            analyze_shared_memory(arrays,{'0':['%p = getelementptr [4 x float], [4 x float] addrspace(3)* @cache, i32 0, i32 0','call void @unknown(float addrspace(3)* %p)','ret void']})

    def test_typed64_and_texture_atomics_roundtrip_and_preserve_types_address_order(self):
        c=Dxc(DLL)
        try:
            for key in ('wide','texture'):
                b,ir=self.fixtures[key];r=reconstruct_hlsl(ir);actual,_=c.compile(r['source'],r['entry'],r['profile']);compiled=c.disassemble(actual)
                self.assertEqual(interface_chunks(b),interface_chunks(actual));self.assertFalse(reflection_differences(ir,compiled));self.assertTrue(compare_dataflow(ir,compiled)['equal'])
                calls=re.findall(r'^  %\d+ = call i(?:32|64) @dx.op.atomicBinOp.+',ir,re.M);first=calls[0]
                for candidate in [ir.replace(first,first.replace('i32 7,','i32 6,',1)),ir.replace(first,re.sub(r'%\d+ =','%unused =',first,count=1)+'\n'+first)]:
                    self.assertNotEqual(candidate,ir)
                    try:flow=compare_dataflow(ir,candidate)
                    except ReconstructionError:continue
                    self.assertFalse(flow['equal'])
                if len(calls)==2:
                    candidate='\n'.join(calls[1] if s==first else first if s==calls[1] else s for s in ir.splitlines())+'\n'
                    self.assertFalse(compare_dataflow(ir,candidate)['equal'])
        finally:c.close()

    def test_wide_layout_and_pack_width_cannot_change(self):
        _,ir=self.fixtures['wide']
        for candidate in [ir.replace('type { i64 }','type { i32 }'),ir.replace('i32 1, i32 8, i32 3, i32 1','i32 1, i32 4, i32 3, i32 1')]:
            self.assertNotEqual(candidate,ir)
            with self.assertRaises(ReconstructionError):compare_dataflow(ir,candidate)
        changed=re.sub(r'(shl(?: nuw)? i64 %\d+, )32',r'\g<1>31',ir)
        self.assertNotEqual(changed,ir);self.assertFalse(compare_dataflow(ir,changed)['equal'])

    def test_new_wave_collectives_keep_opcode_predicate_order_count_and_guard(self):
        _,ir=self.fixtures['wave'];self.assertTrue(compare_dataflow(ir,ir)['equal'])
        first=re.search(r'^  %\d+ = call i32 @dx.op.waveActiveBit.+',ir,re.M)[0]
        second=re.search(r'^  %\d+ = call i32 @dx.op.wavePrefixOp.+',ir,re.M)[0]
        for candidate in [ir.replace(first,re.sub(r'%\d+ =','%unused =',first,count=1)+'\n'+first),
                          '\n'.join(second if s==first else first if s==second else s for s in ir.splitlines())+'\n',
                          ir.replace('i8 1)','i8 2)'),ir.replace('i32 136, i1','i32 135, i1')]:
            self.assertNotEqual(candidate,ir)
            try:flow=compare_dataflow(ir,candidate)
            except ReconstructionError:continue
            self.assertFalse(flow['equal'])


@unittest.skipUnless(DLL.exists() and (ROOT/'inventory.json').exists(),'Original raster IR/DXC required')
class RasterSourceTests(unittest.TestCase):
    def test_all_twelve_original_sources_compile_and_preserve_nested_effects(self):
        inv=json.loads((ROOT/'inventory.json').read_text(encoding='utf-8'));doc=Document.open(inv['source']);c=Dxc(DLL)
        try:
            rows=[r for r in inv['entries'] if r['entry']=='MeshletSoftwareRasterizer'];self.assertEqual(len(rows),12)
            for item in rows:
                with self.subTest(index=item['index']):
                    ir=Path(item['ir']).read_text(encoding='utf-8');r=reconstruct_hlsl(ir)
                    self.assertEqual(len(r['natural_loops']),2);self.assertEqual(r['source'].count('[loop] while (true)'),2)
                    self.assertEqual(r['source'].count('GroupMemoryBarrierWithGroupSync();'),1)
                    emitted=Counter(re.findall(r'// DXIL block (\w+)',r['source']))
                    self.assertEqual(set(emitted.values()),{1});self.assertEqual(len(emitted),r['blocks'])
                    b,_=c.compile(r['source'],r['entry'],r['profile']);actual=c.disassemble(b)
                    self.assertFalse(doc.interface_differences(item['index'],b));self.assertFalse(reflection_differences(ir,actual))
                    self.assertTrue(compare_dataflow(ir,actual)['equal'])
        finally:c.close()


if __name__=='__main__':unittest.main()
