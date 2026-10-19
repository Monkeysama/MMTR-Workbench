"""Second-generation reconstruction must retain precise calls and phi bits."""
import json
from pathlib import Path
import re
import unittest
from mmtr_editor.instruction_metadata import precise_instruction
from mmtr_editor.reconstruct import reconstruct_hlsl,ReconstructionError
from mmtr_editor.dxc_process import compile_hlsl,disassemble_shader
from mmtr_editor.precision_constraints import compare_precision_constraints
from mmtr_editor.verify_ir import Graph,compare_dataflow

ROOT=Path(__file__).resolve().parents[1]
DLL=Path('C:/Software/Kiro/dxcompiler.dll')
COMBINED=ROOT/'output/skin_runtime/mesh_coverage/combined_verification.json'

class PreciseSyntaxTests(unittest.TestCase):
    def test_precise_attachment_requires_resolved_i32_one_and_supported_float_call(self):
        line='%12 = call float @dx.op.unary.f32(i32 21, float %11), !dx.precise !7'
        self.assertEqual(precise_instruction(line,{'7':'i32 1'}),(line.split(', !dx.precise')[0],'%12'))
        for metadata in ({},{'7':'i32 0'},{'7':'i1 true'},{'7':'i32 1, i32 1'}):
            with self.assertRaises(ReconstructionError):precise_instruction(line,metadata)
        for bad in (line+', !unknown !8',line.replace('call float','call i32'),line.replace('!7','!bad')):
            with self.assertRaises(ReconstructionError):precise_instruction(bad,{'7':'i32 1'})

@unittest.skipUnless(DLL.exists(),'Windows DXC required')
class RealPreciseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload,_,cls.ir=compile_hlsl(DLL,'float4 main(float4 x:TEXCOORD0):SV_Target { precise float v=exp2(x.x); return float4(v,v,v,v); }','main','ps_6_6')

    def test_resource_free_marked_pixel_shader_emits_precise_and_preserves_second_compile(self):
        self.assertIn('!dx.precise',self.ir)
        r=reconstruct_hlsl(self.ir);self.assertIn('precise float',r['source'])
        _,_,after=compile_hlsl(DLL,r['source'],r['entry'],r['profile'])
        self.assertTrue(compare_dataflow(self.ir,after)['equal'])
        self.assertTrue(compare_precision_constraints(self.ir,after)['preserved'])
        row=next(i for i,x in enumerate(r['source'].splitlines()) if 'exp2(' in x)
        self.assertTrue(any('!dx.precise' in self.ir.splitlines()[n-1] for n in r['source_map'][row]))

    def test_dropping_marker_is_detected_even_when_value_graph_matches(self):
        unmarked=re.sub(r', !dx.precise !\d+','',self.ir)
        self.assertTrue(compare_dataflow(self.ir,unmarked)['equal'])
        audit=compare_precision_constraints(self.ir,unmarked)
        self.assertFalse(audit['preserved']);self.assertTrue(audit['missing'])
        self.assertTrue(compare_precision_constraints(unmarked,self.ir)['preserved'])

    def test_unknown_precision_metadata_rejected_by_reconstruction_and_graph(self):
        ref=re.search(r'!dx.precise !(\d+)',self.ir)[1]
        changed=re.sub(r'^!'+ref+r' = !\{i32 1\}$','!'+ref+' = !{i32 0}',self.ir,flags=re.M)
        self.assertNotEqual(changed,self.ir)
        for action in (reconstruct_hlsl,Graph):
            with self.assertRaises(ReconstructionError):action(changed)

    def test_fast_math_added_to_a_precise_dependency_is_rejected(self):
        _,_,ir=compile_hlsl(DLL,'float4 main(float4 x:TEXCOORD0):SV_Target { precise float v=exp2(x.x*x.y); return float4(v,v,v,v); }','main','ps_6_6')
        self.assertIn('fmul float',ir)
        changed=ir.replace('fmul float','fmul fast float',1)
        self.assertTrue(compare_dataflow(ir,changed)['equal'])
        audit=compare_precision_constraints(ir,changed)
        self.assertFalse(audit['preserved']);self.assertTrue(audit['weakened_flags'])
        multiply=re.search(r'^  %\d+ = fmul float (.+)$',ir,re.M)[1]
        ref=re.search(r'!dx.precise !(\d+)',ir)[1]
        duplicate=f'  %9999 = fmul fast float {multiply}\n  %10000 = call float @dx.op.unary.f32(i32 21, float %9999), !dx.precise !{ref}\n'
        mixed=ir.replace('  ret void',duplicate+'  ret void')
        self.assertTrue(compare_dataflow(ir,mixed)['equal'])
        self.assertFalse(compare_precision_constraints(ir,mixed)['preserved'])

@unittest.skipUnless(COMBINED.exists() and DLL.exists(),'Local first-generation corpus required')
class RealReentryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items=json.loads(COMBINED.read_text())['shaders']

    def test_original_compiled_mesh_can_be_reconstructed_and_compiled_again(self):
        ir=disassemble_shader(DLL,Path(self.items[298]['artifact']).read_bytes())
        r=reconstruct_hlsl(ir);self.assertEqual(r['profile'],'ms_6_6')
        _,_,after=compile_hlsl(DLL,r['source'],r['entry'],r['profile'])
        self.assertTrue(compare_dataflow(ir,after)['equal'])
        self.assertTrue(compare_precision_constraints(ir,after)['preserved'])

    def test_private_array_tbaa_and_acyclic_undefined_phi_edges(self):
        for index in (2,9,141):
            with self.subTest(index=index):
                ir=disassemble_shader(DLL,Path(self.items[index]['artifact']).read_bytes())
                r=reconstruct_hlsl(ir)
                if index==2:self.assertIn('!tbaa',ir)
                else:self.assertIn('undefined incoming phi',r['source'])
                _,_,after=compile_hlsl(DLL,r['source'],r['entry'],r['profile'])
                self.assertTrue(compare_dataflow(ir,after)['equal'])
                self.assertTrue(compare_precision_constraints(ir,after)['preserved'])

    def test_bitcast_commutes_with_guarded_phi_without_changing_bits_or_guards(self):
        ir=disassemble_shader(DLL,Path(self.items[298]['artifact']).read_bytes())
        g=Graph(ir)
        one=g.value('1.0','float');negzero=g.value('-0.0','float')
        guard=g.bdd(g.intern(('test_condition',)))
        inverse=g.logic('xor',guard,'1')
        phi=g.intern(('guarded_phi','float',tuple(sorted(((one,guard),(negzero,inverse))))))
        actual=g.bitcast32(phi,'i32')
        expected=g.intern(('guarded_phi','i32',tuple(sorted(((g.value('1065353216','i32'),guard),(g.value('2147483648','i32'),inverse))))))
        self.assertEqual(actual,expected)
        self.assertEqual(g.bitcast32(actual,'float'),phi)
        changed=g.intern(('guarded_phi','i32',tuple(sorted(((g.value('0','i32'),guard),(g.value('2147483648','i32'),inverse))))))
        self.assertNotEqual(actual,changed)

if __name__=='__main__':unittest.main()
