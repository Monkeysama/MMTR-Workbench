"""Meshlet geometry packing, unsigned remainder and dynamic raw SRV events."""
import json
from pathlib import Path
import re
import unittest

from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.model import Document
from mmtr_editor.reconstruct import reconstruct_hlsl, ReconstructionError
from mmtr_editor.verify_ir import compare_dataflow
from test_skin_compute import program, store

ROOT=Path('output/skin_runtime/stage_inventory')
DLL=Path('C:/Software/Kiro/dxcompiler.dll')


class GeometryValueTests(unittest.TestCase):
    def test_dynamic_read_with_ambiguous_path_ordinal_is_rejected(self):
        prefix='''  %heap = call %dx.types.Handle @dx.op.createHandleFromHeap(i32 218, i32 0, i1 false, i1 true)
  %srv = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %heap, %dx.types.ResourceProperties { i32 11, i32 0 })
  %t = call i32 @dx.op.threadId.i32(i32 93, i32 0)
  %c = icmp eq i32 %t, 0
  br i1 %c, label %yes, label %end
yes:
  %a = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %srv, i32 0, i32 undef, i8 1, i32 4)
  br label %end
end:
  %b = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %srv, i32 4, i32 undef, i8 1, i32 4)
'''
        ir=program(prefix+store(0,11))
        with self.assertRaisesRegex(ReconstructionError,'事件前缀长度'):
            compare_dataflow(ir,ir)

    def test_integer_bitwise_regrouping_is_exact_but_keeps_values_and_operations(self):
        for op in ('and','or','xor'):
            prefix='  %t = call i32 @dx.op.threadId.i32(i32 93, i32 0)\n'
            a=program(prefix+f'  %a = {op} i32 %t, 3\n  %b = {op} i32 %a, 8\n'+store(0,'%b'))
            b=program(prefix+f'  %a = {op} i32 8, %t\n  %b = {op} i32 3, %a\n'+store(0,'%b'))
            self.assertTrue(compare_dataflow(a,b)['equal'])
            self.assertFalse(compare_dataflow(a,b.replace('i32 3,','i32 4,'))['equal'])
            self.assertFalse(compare_dataflow(a,b.replace(f'%b = {op}', '%b = add'))['equal'])

    def test_legacy_float_pack_shape_opcode_and_value_are_preserved(self):
        pack='  %p = call i32 @dx.op.legacyF32ToF16(i32 130, float 1.500000e+00)\n'
        a=program(pack+store(0,'%p'))
        self.assertTrue(compare_dataflow(a,a)['equal'])
        self.assertFalse(compare_dataflow(a,a.replace('1.500000e+00','1.000000e+00'))['equal'])
        for bad in (pack.replace('i32 130','i32 131'),pack.replace('call i32','call float'),
                    pack.replace('float 1.500000e+00','i32 1'),pack.replace('legacyF32ToF16','legacyF32ToF16.i32')):
            with self.subTest(bad=bad),self.assertRaises(ReconstructionError):
                compare_dataflow(a,program(bad+store(0,'%p')))

    def test_unsigned_remainder_operand_order_and_divisor_remain_observable(self):
        a=program('  %t = call i32 @dx.op.threadId.i32(i32 93, i32 0)\n'
                  '  %p = urem i32 %t, 3\n'+store(0,'%p'))
        self.assertTrue(compare_dataflow(a,a)['equal'])
        for changed in (a.replace('%t, 3','3, %t'),a.replace('%t, 3','%t, 5'),a.replace('urem','udiv'),a.replace('urem','srem')):
            self.assertFalse(compare_dataflow(a,changed)['equal'])


@unittest.skipUnless((ROOT/'inventory.json').exists() and DLL.exists(),'Original geometry IR/DXC required')
class RealGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv=json.loads((ROOT/'inventory.json').read_text(encoding='utf-8'))
        cls.ir={i:Path(next(r for r in cls.inv['entries'] if r['index']==i)['ir']).read_text(encoding='utf-8') for i in (0,159,326)}

    def test_three_geometry_profiles_compile_pack_and_preserve_complete_dataflow(self):
        d=Document.open(self.inv['source']);c=Dxc(DLL)
        try:
            for i,ir in self.ir.items():
                with self.subTest(index=i):
                    r=reconstruct_hlsl(ir)
                    self.assertIn(' % 3u',r['source']);self.assertEqual(r['source'].count('f32tof16('),5)
                    self.assertIn('[numthreads(192, 1, 1)]',r['source'])
                    b,_=c.compile(r['source'],r['entry'],r['profile']);actual=c.disassemble(b)
                    self.assertFalse(d.interface_differences(i,b));self.assertFalse(reflection_differences(ir,actual))
                    self.assertTrue(compare_dataflow(ir,actual)['equal'])
        finally:c.close()

    def test_dynamic_read_descriptor_index_nonuniform_address_order_and_count(self):
        for index,ir in self.ir.items():
            if index==0:
                handle=re.search(r'^  %\d+ = call %dx.types.Handle @dx.op.createHandle\(i32 57, i8 0, i32 0, i32 %\d+, i1 true\).+',ir,re.M)[0]
                name=re.match(r'  (%\d+)',handle)[1]
                descriptor=re.sub(r'i32 %\d+, i1 true', 'i32 0, i1 true',handle)
            else:
                handle=re.search(r'^  %\d+ = call %dx.types.Handle @dx.op.createHandleFromHeap\(i32 218, i32 %\d+, i1 false, i1 true\).+',ir,re.M)[0]
                rawname=re.match(r'  (%\d+)',handle)[1]
                name=re.search(r'^  (%\d+) = call %dx.types.Handle @dx.op.annotateHandle\(i32 216, %dx.types.Handle '+rawname+r',',ir,re.M)[1]
                descriptor=re.sub(r'i32 %\d+, i1 false', 'i32 0, i1 false',handle)
            reads=re.findall(r'^  %\d+ = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32\(i32 139, %dx.types.Handle '+name+r',.+',ir,re.M)
            first,second=reads[:2]
            changed_address=re.sub(r'(Handle '+name+r', i32 )%\d+',r'\g<1>0',first)
            variants=[ir.replace(handle,descriptor),ir.replace(handle,handle.replace('i1 true','i1 false')),
                      ir.replace(first,changed_address),ir.replace(first,re.sub(r'%\d+ =','%unused =',first,count=1)+'\n'+first),
                      '\n'.join(second if s==first else first if s==second else s for s in ir.splitlines())+'\n']
            for n,candidate in enumerate(variants):
                with self.subTest(index=index,mutation=n):
                    self.assertNotEqual(candidate,ir);self.assertFalse(compare_dataflow(ir,candidate)['equal'])

    def test_heap_annotation_mask_alignment_and_postwrite_read_fail_closed(self):
        ir=self.ir[159]
        # Select the actual heap annotation, not one of the static raw SRVs.
        heap=re.search(r'^  (%\d+) = call %dx.types.Handle @dx.op.createHandleFromHeap.+',ir,re.M)[1]
        annotation=re.search(r'^  (%\d+) = call %dx.types.Handle @dx.op.annotateHandle\(i32 216, %dx.types.Handle '+heap+r',.+',ir,re.M)
        read=re.search(r'^  %\d+ = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32\(i32 139, %dx.types.Handle '+annotation[1]+r',.+',ir,re.M)[0]
        write=re.search(r'^  call void @dx.op.rawBufferStore.i32.+',ir,re.M)[0]
        for candidate in [ir.replace(annotation[0],annotation[0].replace('i32 11, i32 0','i32 4107, i32 0')),
                          ir.replace(read,read.replace('i8 15, i32 4','i8 5, i32 4')),
                          ir.replace(read,read.replace('i8 15, i32 4','i8 15, i32 2')),
                          ir.replace(write,write+'\n'+re.sub(r'%\d+ =','%late =',read,count=1))]:
            self.assertNotEqual(candidate,ir)
            with self.assertRaises(ReconstructionError):compare_dataflow(ir,candidate)


if __name__=='__main__':unittest.main()
