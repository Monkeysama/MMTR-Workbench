"""Native fault/timeout containment, protocol rejection and real RPC recovery."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mmtr_editor import dxc_process
from mmtr_editor.dxc_process import DxcProcessError,compile_hlsl,disassemble_shader,assemble_dxil,run_worker
from mmtr_editor.model import Document,EditError,validate_shader
from mmtr_editor.rpc import Service

ROOT=Path(__file__).resolve().parents[1]
DLL=Path('C:/Software/Kiro/dxcompiler.dll')
INVENTORY=ROOT/'output/skin_runtime/stage_inventory/inventory.json'
SIMPLE='float4 main():SV_Target {return float4(.2,.4,.6,1);}'
SCALAR_MESH='''struct V {noperspective float4 p:SV_Position;nointerpolation uint2 m:MATERIAL;};
struct P {nointerpolation uint id:PRIMITIVE_ID;};
[outputtopology("triangle")][numthreads(128,1,1)]
void main(uint t:SV_GroupIndex,out vertices V v[128],out indices uint3 ix[128],out primitives P pr[128]){
 SetMeshOutputCounts(3,1);
 if(t<3){v[t].p.x=0;v[t].p.y=0;v[t].p.z=0;v[t].p.w=1;v[t].m.x=0;v[t].m.y=0;}
 if(t==0){ix[0]=uint3(0,1,2);pr[0].id=0;}
}'''


class DxcTransportTests(unittest.TestCase):
    def test_source_and_frozen_worker_commands(self):
        with patch.object(sys,'frozen',False,create=True):
            self.assertEqual(dxc_process.worker_command(),[sys.executable,'-m','mmtr_editor.dxc_worker'])
        with patch.object(sys,'frozen',True,create=True):
            self.assertEqual(dxc_process.worker_command(),[sys.executable,'--dxc-worker'])

    def test_abrupt_worker_exit_is_reported_without_entering_native_parent(self):
        with patch.object(dxc_process,'worker_command',return_value=[sys.executable,'-c','import os;os._exit(73)']):
            with self.assertRaises(DxcProcessError) as caught:run_worker(DLL,'compile',source='',entry='',profile='')
        self.assertEqual(caught.exception.details(),dict(kind='crash',operation='compile',exit_code=73))
        self.assertIn('0x00000049',str(caught.exception))

    def test_timeout_kills_and_reaps_exact_child(self):
        children=[];real_popen=subprocess.Popen
        def record(*args,**kwargs):
            child=real_popen(*args,**kwargs);children.append(child);return child
        with patch.object(dxc_process,'worker_command',return_value=[sys.executable,'-c','import time;time.sleep(15)']),\
             patch.object(dxc_process.subprocess,'Popen',side_effect=record):
            with self.assertRaises(DxcProcessError) as caught:run_worker(DLL,'disassemble',payload='',timeout=.2)
        self.assertEqual(caught.exception.kind,'timeout');self.assertEqual(len(children),1)
        self.assertIsNotNone(children[0].poll())

    def test_launch_failure_is_reported(self):
        with patch.object(dxc_process,'worker_command',return_value=[str(ROOT/'does-not-exist-worker.exe')]):
            with self.assertRaises(DxcProcessError) as caught:run_worker(DLL,'disassemble',payload='')
        self.assertEqual(caught.exception.kind,'launch')

    def test_assembly_native_exit_is_contained_and_not_a_payload(self):
        with patch.object(dxc_process,'worker_command',return_value=[sys.executable,'-c','import os;os._exit(73)']):
            with self.assertRaises(DxcProcessError) as caught:
                assemble_dxil(DLL,'untrusted IR')
        self.assertEqual(caught.exception.details(),dict(kind='crash',operation='assemble',exit_code=73))

    def test_malformed_truncated_wrong_type_and_invalid_container_responses_rejected(self):
        variants=['garbage','[]','{"protocol":1,"ok":true}',
                  json.dumps(dict(protocol=1,ok=False,error=17)),
                  json.dumps(dict(protocol=1,ok=True,result=dict(ir='IR',warnings='',payload='badbase64'))),
                  json.dumps(dict(protocol=1,ok=True,result=dict(ir='IR',warnings='',payload=base64.b64encode(b'bad').decode()))),
                  json.dumps(dict(protocol=1,ok=True,result=dict(ir='IR',warnings='',payload=base64.b64encode(b'DXBCbad').decode()))),
                  json.dumps(dict(protocol=1,ok=True,result=dict(ir='',warnings='',payload='')))]
        for response in variants:
            code='import sys;sys.stdout.write('+repr(response)+')'
            with self.subTest(response=response),patch.object(dxc_process,'worker_command',return_value=[sys.executable,'-c',code]):
                with self.assertRaises(DxcProcessError) as caught:run_worker(DLL,'compile')
                self.assertEqual(caught.exception.kind,'protocol')

    def test_compile_failure_invalidates_previous_compiled_payload(self):
        service=Service();service.compiled=b'old-success'
        error=DxcProcessError('fault',kind='crash',operation='compile',exit_code=73)
        # 缓存前检使用临时 DLL 身份；原生编译已模拟，不依赖本机 DXC。
        with tempfile.TemporaryDirectory() as temporary:
            dll=Path(temporary)/'dxcompiler.dll';dll.write_bytes(b'synthetic DXC identity')
            with patch('mmtr_editor.rpc.compile_hlsl',side_effect=error),self.assertRaises(DxcProcessError):
                service.handle(dict(op='compile',dll=str(dll),source='',entry='main',profile='ps_6_2'))
        self.assertIsNone(service.compiled)
        with self.assertRaises(EditError):service.handle(dict(op='export_compiled',path='never-written.dxbc'))
        self.assertIsNone(service.handle(dict(op='snapshot')))

    def test_service_decode_uses_worker_and_preserves_document_on_failure(self):
        service=Service();sentinel=object();service.doc=sentinel
        error=DxcProcessError('fault',kind='crash',operation='disassemble',exit_code=73)
        with patch('mmtr_editor.rpc.disassemble_shader',side_effect=error),self.assertRaises(DxcProcessError):
            service.decode(str(DLL),b'DXBC')
        self.assertIs(service.doc,sentinel)

    def test_frozen_entry_dispatch_works_without_frozen_build(self):
        # Executes the exact production entry-point dispatch with a harmless
        # protocol rejection. No release package or native DLL call is needed.
        p=subprocess.run([sys.executable,'scripts/editor_core_entry.py','--dxc-worker'],cwd=ROOT,
                         env={**os.environ,'PYTHONPATH':str(ROOT)},input=b'{"protocol":0}',capture_output=True,timeout=10)
        self.assertEqual(p.returncode,0);r=json.loads(p.stdout)
        self.assertFalse(r['ok']);self.assertIn('protocol',r['error'])


@unittest.skipUnless(DLL.exists(),'Configured local DXC required')
class RealDxcProcessTests(unittest.TestCase):
    def test_assembly_is_validated_and_invalid_ir_does_not_poison_next_request(self):
        payload,_,ir=compile_hlsl(DLL,SIMPLE,'main','ps_6_2')
        with patch('mmtr_editor.dxil_assembly.DxilAssembly',side_effect=AssertionError('No native calls in parent')):
            assembled,messages,actual=assemble_dxil(DLL,ir)
            self.assertEqual(validate_shader(assembled)['shader_model'],validate_shader(payload)['shader_model'])
            self.assertIn('define void @main()',actual)
            self.assertFalse(messages)
            with self.assertRaises(DxcProcessError) as caught:
                assemble_dxil(DLL,'invalid LLVM assembly')
            self.assertEqual(caught.exception.kind,'diagnostic')
            self.assertTrue(assemble_dxil(DLL,ir)[0].startswith(b'DXBC'))

    def test_compile_disassemble_and_real_diagnostics_are_preserved(self):
        # Parent-side Dxc construction is forbidden; children import their own
        # module, so this detects accidental fallback to the document process.
        with patch('mmtr_editor.dxc.Dxc',side_effect=AssertionError('No native calls in parent')):
            payload,warnings,ir=compile_hlsl(DLL,SIMPLE,'main','ps_6_2')
            self.assertEqual(validate_shader(payload)['stage'],'ps');self.assertFalse(warnings)
            self.assertEqual(ir,disassemble_shader(DLL,payload))
            with self.assertRaises(DxcProcessError) as caught:compile_hlsl(DLL,'invalid hlsl','main','ps_6_2')
            self.assertEqual(caught.exception.kind,'diagnostic');self.assertIn('error:',str(caught.exception))

    def test_missing_dll_and_corrupt_input_are_contained(self):
        with self.assertRaises(DxcProcessError):compile_hlsl(ROOT/'missing-dxcompiler.dll',SIMPLE,'main','ps_6_2')
        with self.assertRaises(DxcProcessError):disassemble_shader(DLL,b'invalid shader')
        self.assertTrue(compile_hlsl(DLL,SIMPLE,'main','ps_6_2')[0].startswith(b'DXBC'))

    @unittest.skipUnless(INVENTORY.exists(),'Original runtime corpus required')
    def test_real_rpc_survives_native_mesh_fault_preserves_edits_exports_and_recovers(self):
        inv=json.loads(INVENTORY.read_text(encoding='utf-8'));coverage=ROOT/'output/skin_runtime/mesh_coverage'
        payload=(coverage/'0298/recompiled.dxbc').read_bytes();source=(coverage/'0298/reconstructed.hlsl').read_text(encoding='utf-8')
        expected=Document.open(inv['source']);expected.replace_shader(298,payload);built,_=expected.build()
        with tempfile.TemporaryDirectory() as temporary:
            export=Path(temporary)/'preserved.mmtr.240906211';recovered=Path(temporary)/'recovered.mmtr.240906211'
            requests=[dict(op='open',path=inv['source']),
                      dict(op='replace',index=298,path=str(coverage/'0298/recompiled.dxbc'),dll=str(DLL)),
                      dict(op='compile',source=SIMPLE,entry='main',profile='ps_6_2',dll=str(DLL)),
                      dict(op='compile',source=SCALAR_MESH,entry='main',profile='ms_6_6',dll=str(DLL)),
                      dict(op='snapshot'),dict(op='export',path=str(export)),
                      dict(op='compile',source=source,entry='MS_MeshletStaticVisibilityWrite',profile='ms_6_6',dll=str(DLL)),
                      dict(op='apply_compiled',index=298,dll=str(DLL)),dict(op='export',path=str(recovered))]
            p=subprocess.run([sys.executable,'-u','-m','mmtr_editor.rpc'],cwd=ROOT,
                             input=''.join(json.dumps(r)+'\n' for r in requests).encode(),capture_output=True,timeout=40)
            self.assertEqual(p.returncode,0,p.stderr.decode(errors='replace'));responses=[json.loads(x) for x in p.stdout.splitlines()]
            self.assertEqual(len(responses),len(requests))
            for i in [0,1,2,4,5,6,7,8]:self.assertTrue(responses[i]['ok'],responses[i])
            failure=responses[3]
            if not failure['ok']:
                self.assertEqual(failure['details']['kind'],'crash');self.assertEqual(failure['details']['operation'],'compile')
            # A future fixed compiler may compile the scalar probe; either
            # outcome must leave the actual MMTR edits unchanged.
            self.assertEqual(responses[4]['result']['changes']['shaders'],[298])
            self.assertEqual(export.read_bytes(),built);self.assertEqual(recovered.read_bytes(),built)


if __name__=='__main__':unittest.main()
