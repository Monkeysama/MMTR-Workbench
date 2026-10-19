"""Real-corpus losslessness, variable-length relocation, and invalid-edit checks."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from inspect_mmtr import inspect_data
from mmtr_editor.dxc import Dxc
from mmtr_editor.model import Document, EditError, validate_shader
from mmtr_editor.rpc import Service
from mmtr_editor.hashes import murmur3_x86_32, xxh64

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = Path('F:/MH Wilds/Game Unpack/MHWILDS_EXTRACT/re_chunk_000/natives/STM/MaterialShader/Variation')
VARIATION = Path('F:/MH Wilds/Game Unpack/MHWILDS_EXTRACT/natives/STM/MaterialShader/Variation')
DLL = Path('C:/Software/Kiro/dxcompiler.dll')


def resized_container(data):
    """Extend ILDN debug-name chunk, keeping other chunks and interface payload intact.

    This exercises relocation; not a claim that an internally unsigned container
    is accepted by the game. HLSL compilation is tested separately with DXC.
    """
    info = validate_shader(data)
    chunks = []
    for c in info['chunks']:
        raw = data[c['offset']:c['offset'] + c['size'] + 8]
        if c['tag'] == 'ILDN':
            raw = raw[:4] + struct.pack('<I', c['size'] + 4) + raw[8:] + b'\0'*4
        chunks.append(raw)
    header = bytearray(data[:32 + len(chunks)*4])
    cursor = len(header)
    for i, raw in enumerate(chunks):
        struct.pack_into('<I', header, 32 + i*4, cursor)
        cursor += len(raw)
    struct.pack_into('<I', header, 24, cursor)
    return bytes(header) + b''.join(chunks)


@unittest.skipUnless(SAMPLES.is_dir(), 'Local game research corpus unavailable')
class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skin = Document.open(SAMPLES / 'Base_ATOS_SkinEdit_VFX.mmtr.250206176')

    def setUp(self):
        self.skin.reset()

    def test_four_samples_exact_noop(self):
        results = []
        for name in ['Base_ALBA_Emit_FX_Transparent', 'Base_ATOS_SkinEdit_VFX', 'BaseAlpha_MatCap_FX', 'Base_ALBA_NRRT_FX_Detail']:
            with self.subTest(name=name):
                doc = Document.open(SAMPLES / (name + '.mmtr.250206176'))
                self.assertFalse(doc.integrity['name_hash_mismatches'])
                self.assertFalse(doc.integrity['shader_trailer_mismatches'])
                rebuilt, report = doc.build()
                self.assertEqual(rebuilt, doc.original)
                with tempfile.TemporaryDirectory() as temp:
                    out = Path(temp) / 'copy.mmtr.250206176'
                    doc.export(out)
                    self.assertEqual(out.read_bytes(), doc.original)
                results.append({'sample': name, 'sha256': report['sha256'], 'byte_identical': True})
        (ROOT / 'output').mkdir(exist_ok=True)
        (ROOT / 'output' / 'editor_verification.json').write_text(json.dumps(results, indent=2), encoding='utf-8')

    def test_variable_length_relocation_all_references(self):
        doc = self.skin
        index = 25
        payload = resized_container(doc.shader_bytes(index))
        self.assertEqual(len(payload), len(doc.shader_bytes(index)) + 4)
        doc.replace_shader(index, payload)
        rebuilt, report = doc.build()
        _, parsed = inspect_data(rebuilt, doc.source)
        for old, new in zip(doc.info['shaders'], parsed['shaders']):
            self.assertEqual(new['offset'], old['offset'] + (4 if old['index'] > index else 0))
            raw = rebuilt[new['offset']:new['trailer_offset']]
            self.assertEqual(raw, doc.shader_bytes(new['index']))
            self.assertEqual(xxh64(raw, 0xffffffff), int.from_bytes(bytes.fromhex(new['trailer_hex']), 'little'))
        for record in parsed['records']:
            for ref in record['shader_references']:
                self.assertEqual(ref['offset'], parsed['shaders'][ref['shader_index']]['offset'])
                self.assertEqual(ref['size'], parsed['shaders'][ref['shader_index']]['size'])
        self.assertTrue(report['reparse_valid'])
        self.assertEqual(len(rebuilt), len(doc.original)+4)

    def test_shared_rename_updates_all_metadata_hashes(self):
        doc = self.skin
        row = next(r for r in doc.name_rows.values() if r['name']=='ColorParam')
        doc.rename(row['name_offset'], 'ColorTest')
        rebuilt, _ = doc.build()
        reopened = Document(rebuilt, doc.source)
        self.assertFalse(reopened.integrity['name_hash_mismatches'])
        hits = [r for r in reopened.name_rows.values() if r['name']=='ColorTest']
        self.assertTrue(hits)
        self.assertTrue(all(r['name_hash_raw']==murmur3_x86_32(b'ColorTest',0xffffffff) for r in hits))
        self.assertEqual(len(rebuilt),len(doc.original))

    def test_metadata_rejection_is_transactional(self):
        row = next(r for r in self.skin.name_rows.values() if r['name']=='ColorParam')
        for value in ['x'*100, '', '中文', 'bad\0name']:
            with self.assertRaises(EditError):
                self.skin.rename(row['name_offset'],value)
        self.assertFalse(self.skin.dirty)

    def test_malformed_wrong_stage_and_extra_bytes_rejected(self):
        doc = self.skin
        for payload in [b'bad',doc.shader_bytes(1),doc.shader_bytes(25)+b'extra']:
            with self.assertRaises(EditError):
                doc.replace_shader(25,payload,allow_interface_change=True)
        self.assertFalse(doc.dirty)

    def test_vs_alias_redirect_and_source_protection(self):
        doc = self.skin
        record = next(r for r in doc.info['records'] if any(x['field']==16 for x in r['shader_references']))
        source = next(x['shader_index'] for x in record['shader_references'] if x['field']==16)
        target = next(s['index'] for s in doc.info['shaders'] if s['stage']=='vs' and s['shader_model']==doc.info['shaders'][source]['shader_model'] and s['index']!=source)
        doc.redirect(record['index'],16,target,allow_interface_change=True)
        self.assertEqual(doc.effective_shader(record['index'],8),target)
        self.assertEqual(doc.effective_shader(record['index'],16),target)
        data, _ = doc.build()
        reopened = Document(data,doc.source)
        self.assertEqual(reopened.effective_shader(record['index'],8),target)
        with self.assertRaises(EditError):
            doc.export(doc.source)
        self.assertEqual(hashlib.sha256(doc.source.read_bytes()).hexdigest(),doc.info['sha256'])

    def test_failed_open_keeps_current_document(self):
        service=Service();service.doc=self.skin
        with tempfile.TemporaryDirectory() as temp:
            bad=Path(temp)/'bad.mmtr';bad.write_bytes(b'no')
            with self.assertRaises(EditError):
                service.handle({'op':'open','path':str(bad)})
        self.assertIs(service.doc,self.skin)


@unittest.skipUnless(VARIATION.is_dir(), 'Local Variation research corpus unavailable')
class TransparentVariationTests(unittest.TestCase):
    def test_vfx_transparent_family_stops_at_declared_record_table(self):
        """VFX Transparent 的记录后 metadata 不能被误读为第 337 条 pass。"""
        files = sorted(VARIATION.glob('VFX_Transparent*.mmtr.*'))
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.name):
                document = Document.open(path)
                self.assertEqual(document.info['record_count'], 336)
                self.assertEqual(document.info['shader_count'], 165)
                rebuilt, report = document.build()
                self.assertEqual(rebuilt, document.original)
                self.assertTrue(report['unchanged'])


@unittest.skipUnless(DLL.is_file(), 'Microsoft DXC unavailable')
class DxcTests(unittest.TestCase):
    def test_hlsl_compile_and_diagnostics(self):
        dxc=Dxc(DLL)
        try:
            payload, _ = dxc.compile('float4 main(float4 p : SV_Position) : SV_Target0 { return float4(0.2,0.4,0.6,1); }','main','ps_6_2')
            self.assertEqual(validate_shader(payload)['stage'],'ps')
            self.assertIn('define void @main()',dxc.disassemble(payload))
            with self.assertRaises(ValueError):
                dxc.compile('not valid hlsl','main','ps_6_2')
        finally:
            dxc.close()


if __name__=='__main__':
    unittest.main()
