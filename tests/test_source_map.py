"""Emission origins: absolute rows, merged mesh stores, phi edges and prefixes."""
import json
from pathlib import Path
import re
import unittest
from mmtr_editor.reconstruct import reconstruct_hlsl
from mmtr_editor.source_map import IRSourceIndex,expand_source_map
from mmtr_editor.rpc import Service
from inspect_mmtr import sha256

ROOT=Path(__file__).resolve().parents[1]
INVENTORY=ROOT/'output/skin_runtime/stage_inventory/inventory.json'


class SourceIndexTests(unittest.TestCase):
    def test_absolute_locations_distinguish_identical_instructions_and_named_blocks(self):
        ir='; header\ndefine void @main() {\n  br label %next.\nnext.:\n  call void @effect()\n  call void @effect()\n  ret void\n}\n'
        index=IRSourceIndex(ir)
        self.assertEqual(index.positions,{('0',0):3,('next.',0):5,('next.',1):6,('next.',2):7})
        self.assertEqual(index.labels,{'0':2,'next.':4});self.assertEqual(index.terms,{'0':3,'next.':7})
    def test_multiline_generator_items_and_unmapped_declarations(self):
        self.assertEqual(expand_source_map(['struct S {\n};','return;'],{1:(9,8,9)}),[[],[],[8,9]])


@unittest.skipUnless(INVENTORY.exists(),'Local skin corpus required')
class RealSourceMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv=json.loads(INVENTORY.read_text(encoding='utf-8'))
    def reconstruct(self,index):
        item=next(x for x in self.inv['entries'] if x['index']==index)
        ir=Path(item['ir']).read_text(encoding='utf-8');return ir,reconstruct_hlsl(ir)
    def test_merged_mesh_output_keeps_every_component_store_origin(self):
        ir,r=self.reconstruct(298);lines=ir.splitlines()
        source_row=next(i for i,line in enumerate(r['source'].splitlines()) if re.search(r'meshVertices\[.+\.field_0 = float4\(',line))
        origins=r['source_map'][source_row];self.assertEqual(len(origins),4)
        for lane,n in enumerate(origins):self.assertIn(f'i8 {lane}, float',lines[n-1]);self.assertIn('@dx.op.storeVertexOutput.f32',lines[n-1])
    def test_phi_edge_copies_retain_phi_and_actual_predecessor_branch(self):
        ir,r=self.reconstruct(298);lines=ir.splitlines()
        for row,line in enumerate(r['source'].splitlines()):
            match=re.search(r'edge_(\w+)_(\w+)_(\d+) =',line)
            if not match:continue
            origins=r['source_map'][row];self.assertGreaterEqual(len(origins),2)
            self.assertTrue(any(f'%{match[3]} = phi ' in lines[n-1] for n in origins))
            self.assertTrue(any(lines[n-1].strip().startswith('br ') for n in origins))
    def test_nested_prefix_keeps_physical_line_offsets(self):
        ir,r=self.reconstruct(157);self.assertTrue(r['source'].startswith('// Nested-loop'))
        self.assertEqual(r['source_map'][:2],[[],[]]);self.assertEqual(len(r['source_map']),len(r['source'].splitlines()))
        for row,line in enumerate(r['source'].splitlines()):
            m=re.search(r'uint v_(\d+) =',line)
            if m:
                self.assertTrue(any(ir.splitlines()[n-1].strip().startswith('%'+m[1]+' = ') for n in r['source_map'][row]))
    @unittest.skipUnless(Path('C:/Software/Kiro/dxcompiler.dll').exists(),'DXC required')
    def test_native_half_prefix_keeps_physical_line_offsets(self):
        service=Service();service.handle(dict(op='open',path=self.inv['source']))
        r=service.handle(dict(op='reconstruct',index=161,dll='C:/Software/Kiro/dxcompiler.dll'))
        ir=r['origin_ir']
        self.assertTrue(r['source'].startswith('#define MMTR_NATIVE_16BIT_TYPES 1\n'))
        self.assertEqual(r['source_map'][0],[])
        self.assertEqual(len(r['source_map']),len(r['source'].splitlines()))
        mapped=0
        for row,line in enumerate(r['source'].splitlines()):
            m=re.search(r'float16_t v_(\d+) =',line)
            if m:
                self.assertTrue(any(ir.splitlines()[n-1].strip().startswith('%'+m[1]+' = ') for n in r['source_map'][row]))
                mapped+=1
        self.assertGreater(mapped,0)
    @unittest.skipUnless(Path('C:/Software/Kiro/dxcompiler.dll').exists(),'DXC required')
    def test_rpc_snapshot_provenance_is_current_payload_not_a_filename_guess(self):
        service=Service();service.handle(dict(op='open',path=self.inv['source']))
        result=service.handle(dict(op='reconstruct',index=298,dll='C:/Software/Kiro/dxcompiler.dll'))
        self.assertEqual(result['origin_shader_index'],298)
        self.assertEqual(result['origin_shader_sha256'],sha256(service.doc.shader_bytes(298)))
        self.assertEqual(result['origin_document'],str(service.doc.source))
        self.assertIn('define void @MS_',result['origin_ir']);self.assertEqual(len(result['source_map']),len(result['source'].splitlines()))


if __name__=='__main__':unittest.main()
