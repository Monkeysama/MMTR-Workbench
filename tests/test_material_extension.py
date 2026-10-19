"""Workbench自动接口扩展的RPC回归；覆盖事务拒绝、编译产物及真实布局对照。"""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mmtr_editor.dxc import Dxc
from mmtr_editor.mdf import MdfDocument, _public_data
from mmtr_editor.model import Document, EditError
from mmtr_editor.rpc import Service
from mmtr_editor.material_addresses import MaterialTailLayout, material_reads
from mmtr_editor.material_extension import patch_declarations, reflection_container, replace_chunks, chunks, extend_material_interface
from mmtr_editor.dxil_assembly import DxilAssembly
from inspect_mmtr import reflection
from mmtr_editor.hashes import murmur3_x86_32

ROOT = Path(__file__).resolve().parents[1]
DXC = Path('C:/Software/Kiro/dxcompiler.dll')
V2 = ROOT/'output/pants-interface-test-v2-20261019'
OUT = ROOT/'output/workbench-material-layout-sync-20261019'
MANIFEST = V2/'manifest.json'


# 真实侧车Service持有的文档在拒绝路径必须保持同一内存状态，不依赖GUI或GPU。
@unittest.skipUnless(MANIFEST.is_file() and DXC.is_file(), '本地Pants/DXC语料不可用')
class MaterialExtensionRpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))

    def service(self):
        service = Service()
        service.doc = Document.open(self.manifest['source_mmtr'])
        service.mdf = MdfDocument.open(V2/'backup/original.mdf2.45')
        service.mdf_selected = 5
        return service

    def parameter_request(self):
        return {'op': 'mmtr_add_cbuffer_member', 'cbuffer': 'UserMaterial', 'name': 'TestParam',
                'byte_size': 16, 'byte_offset': 960, 'auto_sync': True, 'dll': str(DXC)}

    def test_missing_or_unlinked_mdf_rejects_without_mutation(self):
        service = self.service()
        service.mdf = None
        with self.assertRaisesRegex(EditError, 'MDF'):
            service.handle(self.parameter_request())
        self.assertFalse(service.doc.dirty)
        service = self.service()
        service.doc.source = ROOT/'Unrelated.mmtr.240906211'
        with self.assertRaisesRegex(EditError, '没有使用'):
            service.handle(self.parameter_request())
        self.assertFalse(service.doc.dirty)

    def test_assembler_failure_rolls_back_whole_extension(self):
        service = self.service()
        with patch('mmtr_editor.dxil_assembly.DxilAssembly.assemble', side_effect=ValueError('injected assembler failure')):
            with self.assertRaisesRegex(ValueError, 'injected'):
                extend_material_interface(service.doc, service._material_for_extension(), DXC,
                    parameter={'name': 'TestParam', 'byte_size': 16, 'byte_offset': 960})
        self.assertFalse(service.doc.dirty)
        self.assertEqual(service.doc.build()[0], service.doc.original)

    def test_all_supported_parameter_widths_compile_and_reflect(self):
        service = self.service()
        original = service.doc.shader_bytes(22)
        compiler = DxilAssembly(DXC)
        try:
            executable = compiler.disassemble(original)
            reflected = compiler.disassemble(reflection_container(original))
            for width in (4, 8, 12, 16):
                with self.subTest(byte_size=width):
                    parameter = {'name': 'WidthParam', 'byte_offset': 960, 'byte_size': width}
                    actual, _ = compiler.assemble(patch_declarations(executable, 960, 976, parameter)[0])
                    stat, _ = compiler.assemble(patch_declarations(reflected, 960, 976, parameter)[0])
                    result, _ = compiler.validate(replace_chunks(actual, {'STAT': chunks(stat)['STAT']}))
                    cb = next(cb for cb in reflection(compiler.disassemble(result))['cbuffers'] if cb['name'] == 'UserMaterial')
                    self.assertEqual(cb['size'], 976)
                    self.assertEqual(cb['variables'][-1]['offset'], 960)
                    self.assertEqual(Document._reflection_member_size(cb['variables'][-1]['type']), width)
        finally:
            compiler.close()

    def test_unknown_or_mismatched_layout_rejects_without_mutation(self):
        service = self.service()
        # 同master材质不能使用不同的资源顺序，避免以所选材质布局覆盖其它用户。
        service.mdf.material(5)['textures'].reverse()
        service.mdf.structural_materials.add(5)
        with self.assertRaisesRegex(EditError, '布局不同'):
            service.handle(self.parameter_request())
        self.assertFalse(service.doc.dirty)
        service = self.service()
        with patch('mmtr_editor.material_extension.relocate_material_reads', side_effect=ValueError('unknown material read')):
            with self.assertRaisesRegex(ValueError, 'unknown'):
                extend_material_interface(service.doc, service._material_for_extension(), DXC,
                    parameter={'name': 'TestParam', 'byte_size': 16, 'byte_offset': 960})
        self.assertFalse(service.doc.dirty)

    def test_native_worker_crash_timeout_and_bad_result_preserve_document(self):
        for outcome in (subprocess.CompletedProcess([], 0xc0000005, b'', b''),
                        subprocess.TimeoutExpired('worker', 1),
                        subprocess.CompletedProcess([], 0, b'not json', b'')):
            with self.subTest(outcome=type(outcome).__name__):
                service = self.service()
                # 保留一项原有未导出修改，确认失败不只是保住空文档。
                service.doc.replace_shader(0, service.doc.shader_bytes(0))
                before = dict(service.doc.replacements)
                mock = dict(side_effect=outcome) if isinstance(outcome, Exception) else dict(return_value=outcome)
                with patch('mmtr_editor.material_process.subprocess.run', **mock):
                    with self.assertRaises(EditError):
                        service.handle(self.parameter_request())
                self.assertEqual(service.doc.replacements, before)
                self.assertFalse(service.doc.metadata_edits)

    def test_real_rpc_append_export_preserves_original_material_reads_and_bindings(self):
        service = self.service()
        original = Document.open(self.manifest['source_mmtr'])
        old_mdf = MdfDocument.open(V2/'backup/original.mdf2.45')
        material = old_mdf.material(5)
        result = service.handle(self.parameter_request())
        self.assertGreater(result['interface_sync']['changed_shaders'], 100)
        service.handle({'op': 'mdf_add_property', 'material_index': 5, 'name': 'TestParam', 'values': [0.0]*4})
        result = service.handle({'op': 'mmtr_add_resource', 'kind': 'srvs', 'record_index': 97,
                                 'name': 'TestTexture', 'auto_sync': True, 'dll': str(DXC)})
        self.assertEqual(result['interface_sync']['srv_records'], 192)
        service.handle({'op': 'mdf_add_texture', 'material_index': 5, 'name': 'TestTexture',
                        'path': 'systems/rendering/NullBlack.tex'})
        OUT.mkdir(exist_ok=True)
        target = OUT/'candidate.mmtr.240906211'
        report = service.handle({'op': 'export', 'path': str(target), 'dll': str(DXC)})
        candidate = Document.open(target)
        self.assertFalse(candidate.integrity['name_hash_mismatches'])
        self.assertFalse(candidate.integrity['shader_trailer_mismatches'])
        self.assertEqual(len(report['changed_shaders']), 162)
        mdf_bytes, _ = service.mdf.build()
        (OUT/'candidate.mdf2.45').write_bytes(mdf_bytes)
        new_mdf = MdfDocument(mdf_bytes)
        for before, after in zip(old_mdf.materials, new_mdf.materials):
            if before['index'] != 5:
                self.assertEqual(_public_data(before), _public_data(after))
        prop = new_mdf.material(5)['properties'][-1]
        texture = new_mdf.material(5)['textures'][-1]
        self.assertEqual((prop['name'], prop['offset'], prop['values']), ('TestParam', 960, [0.0]*4))
        for name, actual in [('TestParam', (prop['_unknown_a'], prop['_unknown_b'])),
                             ('TestTexture', struct.unpack_from('<II', texture['_raw_header'], 8))]:
            self.assertEqual(actual, tuple(murmur3_x86_32(name.encode(e), 0xffffffff) for e in ('utf-16-le', 'utf-8')))
        self.assertEqual(texture['path'], 'systems/rendering/NullBlack.tex')
        # 对所有实际编译后的旧SSA读取独立比较字段身份，不复用扩展器的计划判断。
        old = MaterialTailLayout(960, tuple(t['name'] for t in material['textures']), tuple(g['name'] for g in material['gpbf']))
        new = MaterialTailLayout(976, old.texture_names+('TestTexture',), old.gpbf_names)
        decoder = Dxc(DXC)
        count, srv_records = 0, 0
        try:
            for shader in original.info['shaders']:
                index = shader['index']
                before = material_reads(decoder.disassemble(original.shader_bytes(index)))
                after = material_reads(decoder.disassemble(candidate.shader_bytes(index)))
                actual = {ex['ssa']: ex['offset'] for r in after for ex in r['extracts']}
                for read in before:
                    for ex in read['extracts']:
                        expected = ex['offset'] if read['kind'] == 'instance' else old.offset_in(ex['offset'], new)
                        self.assertEqual(actual[ex['ssa']], expected, (index, ex))
                        count += 1
                self.assertTrue(all(r['stride']['bytes'] == 976 for r in after if r['kind'] == 'instance'))
        finally:
            decoder.close()
        # 逐记录验证原绑定行保持、新增PS寄存器行、阶段和总数；未知状态字节保持。
        data = candidate.original
        for before, after in zip(original.info['records'], candidate.info['records']):
            offset = after['offset']
            self.assertEqual(struct.unpack_from('<I', data, offset+0xd4)[0], sum(sum(v) for v in after['per_stage_counts_raw'].values()))
            count_before = len(before['metadata_tables']['srvs'])
            if len(after['metadata_tables']['srvs']) == count_before+1:
                srv_records += 1
                old_pointer = struct.unpack_from('<Q', original.original, offset+0x88)[0]
                pointer = struct.unpack_from('<Q', data, offset+0x88)[0]
                self.assertEqual(data[pointer:pointer+count_before*8], original.original[old_pointer:old_pointer+count_before*8])
                row = struct.unpack_from('<II', data, pointer+count_before*8)
                self.assertEqual(row[0], 0)
                self.assertEqual(row[1] & 0xffff0000, 0x02100000)
                counts = list(before['per_stage_counts_raw']['srvs'])
                counts[4] += 1
                self.assertEqual(after['per_stage_counts_raw']['srvs'], counts)
            self.assertEqual(data[offset+0x40:offset+0x70], original.original[offset+0x40:offset+0x70])
        self.assertEqual(srv_records, 192)
        self.assertEqual(count, 13236)
        # 后续绕过自动接口重换Shader，旧迁移证明必须失效且导出拒绝。
        service.doc.replace_shader(228, original.shader_bytes(228), allow_interface_change=True)
        validation = service.doc.validate_metadata_interfaces(DXC)
        self.assertFalse(validation['valid'])
        self.assertTrue(any(issue['kind'] == 'material_layout' for issue in validation['issues']))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(EditError):
                service.handle({'op': 'export', 'path': str(Path(folder)/'unsafe.mmtr'), 'dll': str(DXC)})
        evidence = dict(passed=True, scalar_reads=count, srv_records=srv_records, other_materials_unchanged=59,
                        changed_shaders=len(report['changed_shaders']), shaders=557,
                        mmtr_sha256=hashlib.sha256(data).hexdigest(), mdf_sha256=hashlib.sha256(mdf_bytes).hexdigest(),
                        game_installed=False, game_tested=False)
        (OUT/'rpc-check.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
