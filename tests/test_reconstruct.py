"""Tests for the first proven reconstruction slice and conservative rejection."""
from pathlib import Path
import tempfile
import unittest

from mmtr_editor.reconstruct import reconstruct_hlsl, ReconstructionError
from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.rpc import Service
from scripts.verify_reconstruction import normalized_body
from tests.local_corpus import MHWILDS_VARIATION

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / 'output/variation_scan/candidates'
SIMPLE = CANDIDATES / 'ad5f36212bb73a035f606dbfb791bb7d9ebae94b274f8454542747f7d62e707f'
DLL = Path('C:/Software/Kiro/dxcompiler.dll')
SKIN = MHWILDS_VARIATION / 'Base_ATOS_SkinEdit_VFX.mmtr.250206176'


@unittest.skipUnless((SIMPLE / 'original.ll').exists(), 'Run Variation scan to obtain corpus fixture')
class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.ir = (SIMPLE / 'original.ll').read_text(encoding='utf-8')

    def test_signatures_and_bit_reinterpretation(self):
        result = reconstruct_hlsl(self.ir)
        self.assertEqual(result['profile'], 'ps_6_6')
        self.assertIn('nointerpolation uint3', result['source'])
        self.assertIn('asuint(input.field_0.z)', result['source'])
        self.assertIn('SV_Target0', result['source'])

    def test_unknown_instruction_and_behavior_flags_rejected(self):
        for changed in (self.ir.replace('ret void', 'br label %next'),
                        self.ir.replace('i64 8388864', 'i64 8388928'),
                        self.ir.replace('i8 2, i32 %3)', 'i8 9, i32 %3)'),
                        self.ir.replace('i8 1, i32 %3)', 'i8 9, i32 %3)')):
            if changed != self.ir:
                with self.assertRaises(ReconstructionError):
                    reconstruct_hlsl(changed)

    def test_real_resource_shader_unknown_operation_rejected(self):
        path = CANDIDATES / '22a9e43275e7fdc36e57545fbc2cbdf445f3f4f0ddc23931b44a2da0624d1cda/original.ll'
        ir = path.read_text(encoding='utf-8')
        # This resource subset is now supported. Keep the original intent:
        # unknown calls may never become invented source or a silent stub.
        changed = ir.replace('@dx.op.', '@dx.op.unknownOperation_', 1)
        self.assertNotEqual(ir,changed)
        with self.assertRaises(ReconstructionError):
            reconstruct_hlsl(changed)

    @unittest.skipUnless(DLL.exists() and SKIN.exists(), 'Local DXC and MMTR required')
    def test_rpc_reconstruct_compile_strict_replace_export(self):
        service = Service()
        service.handle({'op': 'open', 'path': str(SKIN)})
        result = service.handle({'op': 'reconstruct', 'index': 139, 'dll': str(DLL)})
        compiled = service.handle({'op': 'compile', 'dll': str(DLL), **result})
        dxc = Dxc(DLL)
        try:
            original_ir = dxc.disassemble(service.doc.shader_bytes(139))
        finally:
            dxc.close()
        self.assertEqual(normalized_body(original_ir), normalized_body(compiled['ir']))
        self.assertFalse(reflection_differences(original_ir, compiled['ir']))
        service.handle({'op': 'apply_compiled', 'index': 139, 'dll': str(DLL), 'expert': False})
        self.assertFalse(service.doc.replacement_notes[139]['expert_override'])
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / SKIN.name
            service.doc.export(out)
            reread = type(service.doc).open(out)
            self.assertEqual(reread.shader_bytes(139), service.compiled)
            self.assertFalse(reread.integrity['shader_trailer_mismatches'])


if __name__ == '__main__':
    unittest.main()
