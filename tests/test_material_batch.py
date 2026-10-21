"""批量计划/事务的独立合成测试；不依赖游戏语料或原生 DXC。"""
from dataclasses import FrozenInstanceError
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_mdf_editing import fixture as mdf_fixture
from test_structure import fixture as mmtr_fixture
from test_material_addresses import cb_ir
from mmtr_editor.material_addresses import MaterialTailLayout, material_reads, relocate_material_reads
from mmtr_editor.material_extension import append_metadata, patch_declarations, patch_material_properties
from mmtr_editor.material_service import prepare_material_batch
from mmtr_editor.mdf import MdfDocument
from mmtr_editor.model import Document, EditError
from mmtr_editor.rpc import Service
from mmtr_editor.hashes import xxh64
from mmtr_editor.dxc_process import compile_hlsl
from mmtr_editor.structure import parse_structure
from mmtr_editor.dxil_sources import parse, handle_sources


# 固定两材质共用一个 master，预先保留一项未导出修改，失败必须保留该修改。
class MaterialBatchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dll = Path(self.folder.name)/'fake.dll'
        self.dll.write_bytes(b'identity only; never loaded')
        self.service = Service()
        self.service.doc = Document(mmtr_fixture(), Path('MaterialShader/test.mmtr.240906211'))
        self.service.mdf = MdfDocument(mdf_fixture())
        self.service.mdf_selected = 0
        self.service.mdf.set_property(0, 'Value', [0.75])
        self.before = self.service.doc.build()[0], self.service.mdf.build()[0]

    def plan(self, actions, **extra):
        return prepare_material_batch(self.service, self.dll, actions, 0, **extra)[0]

    def test_all_widths_preserve_legacy_alignment_and_action_order(self):
        actions = [{'action': 'parameter', 'name': f'P{width}', 'byte_size': width} for width in (4, 8, 12, 16)]
        actions.insert(1, {'action': 'texture', 'name': 'MapA'})
        actions.append({'action': 'texture', 'name': 'MapB'})
        plan = self.plan(actions, source_revision=7)
        self.assertEqual([p.byte_offset for p in plan.parameters], [16, 32, 48, 64])
        self.assertEqual(plan.new.parameter_bytes, 80)
        self.assertEqual(plan.new.texture_names, ('BaseMap', 'MapA', 'MapB'))
        self.assertEqual(plan.target_materials, (0, 1))
        self.assertEqual(plan.source_revision, 7)
        self.assertEqual(len(plan.digest), 64)
        with self.assertRaises(FrozenInstanceError):
            plan.source_revision = 8
        self.assertEqual(self.before, (self.service.doc.build()[0], self.service.mdf.build()[0]))

    def test_bad_actions_values_and_conflicting_names_fail_before_worker(self):
        failures = [[], [{'action': 'delete', 'name': 'P'}],
            [{'action': 'parameter', 'name': 'P', 'byte_size': 3}],
            [{'action': 'parameter', 'name': 'P', 'byte_size': 4, 'byte_offset': 20}],
            [{'action': 'parameter', 'name': 'P', 'byte_size': 4, 'values': [float('nan')]}],
            [{'action': 'parameter', 'name': 'P', 'byte_size': 4, 'values': [1e100]}],
            [{'action': 'parameter', 'name': 'Value', 'byte_size': 4}],
            [{'action': 'texture', 'name': 'P'}, {'action': 'parameter', 'name': 'P', 'byte_size': 4}],
            [{'action': 'texture', 'name': 'P', 'path': '\0'}],
            [{'action': 'parameter', 'name': 'P', 'byte_size': 4, 'unexpected': 0}]]
        with patch('mmtr_editor.material_process.subprocess.run') as worker:
            for actions in failures:
                with self.subTest(actions=actions), self.assertRaises(EditError):
                    self.plan(actions)
                self.assertEqual(self.before, (self.service.doc.build()[0], self.service.mdf.build()[0]))
            worker.assert_not_called()

    def test_shared_layout_disagreement_rejects_all_targets(self):
        self.service.mdf.add_texture(1, 'Different', 'systems/rendering/NullBlack.tex')
        before = self.service.doc.build()[0], self.service.mdf.build()[0]
        with self.assertRaisesRegex(EditError, '布局不同'):
            self.plan([{'action': 'texture', 'name': 'Added'}])
        self.assertEqual(before, (self.service.doc.build()[0], self.service.mdf.build()[0]))

    def test_preview_identity_tracks_mdf_defaults_revision_and_dxc(self):
        actions = [{'action': 'parameter', 'name': 'P', 'byte_size': 4}]
        original = self.plan(actions)
        self.assertNotEqual(original.digest, self.plan(actions, source_revision=1).digest)
        self.service.mdf.set_property(0, 'Value', [0.25])
        self.assertNotEqual(original.digest, self.plan(actions).digest)
        self.dll.write_bytes(b'another DLL')
        self.assertNotEqual(original.dxc_sha256, self.plan(actions).dxc_sha256)

    def test_worker_crash_timeout_and_stale_plan_preserve_both_documents(self):
        request = {'op': 'material_apply', 'actions': [{'action': 'parameter', 'name': 'P', 'byte_size': 4},
                   {'action': 'texture', 'name': 'Map'}], 'record_index': 0, 'dll': str(self.dll)}
        with self.assertRaisesRegex(EditError, '计划身份'):
            self.service.handle({**request, 'expected_plan': 'stale'})
        for outcome in (subprocess.CompletedProcess([], 0xc0000005, b'', b''),
                        subprocess.TimeoutExpired('worker', 1), subprocess.CompletedProcess([], 0, b'invalid', b'')):
            opts = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
            with patch('mmtr_editor.material_process.subprocess.run', **opts):
                with self.assertRaises(EditError):
                    self.service.handle(request)
            self.assertEqual(self.before, (self.service.doc.build()[0], self.service.mdf.build()[0]))

    def test_batch_metadata_appends_tables_once_and_preserves_old_bindings(self):
        actions = [{'action': 'parameter', 'name': 'P', 'byte_size': 4},
                   {'action': 'texture', 'name': 'MapA'}, {'action': 'texture', 'name': 'MapB'}]
        plan = self.plan(actions)
        operation = {'parameters': [p.declaration() for p in plan.parameters],
                     'textures_added': [t.name for t in plan.textures], 'new_size': plan.new.parameter_bytes,
                     'tables': [{'records': list(records), 'registers': {str(r): [4, 5] for r in records}}
                                for _, records in plan.tables]}
        doc = self.service.doc
        rebuilt = append_metadata(doc.original, doc.source, operation)
        candidate = Document(rebuilt, doc.source)
        buffer = next(e for e in candidate.info['metadata']['cbuffers'] if e['name'] == 'UserMaterial')
        self.assertEqual(buffer['buffer_size'], 32)
        self.assertEqual([(m['name'], m['byte_offset']) for m in buffer['members']], [('Value', 0), ('P', 16)])
        model = parse_structure(rebuilt, doc.source)
        for program in model.programs:
            bindings = program.tables[2].bindings
            self.assertEqual([b.name for b in bindings], ['BaseMap', 'MapA', 'MapB'])
            self.assertEqual([b.slots[4] for b in bindings], [3, 4, 5])
            self.assertEqual(bindings[0].raw, bytes([0, 0, 0, 0, 3, 0, 16, 2]))
            self.assertEqual(program.total_stage_bindings, 4)
        self.assertEqual(doc.original, self.before[0])

    def test_final_layout_migrates_old_texture_gpbf_and_rejects_unknown_address(self):
        old = MaterialTailLayout(16, ('BaseMap',), ('GPBF',))
        new = MaterialTailLayout(48, ('BaseMap', 'MapA', 'MapB'), ('GPBF',))
        result, _ = relocate_material_reads(cb_ir(1, (0, 1)), old, new)
        offsets = [e['offset'] for r in material_reads(result) for e in r.get('extracts', [])]
        self.assertEqual(offsets, [48, 60])
        with self.assertRaises(ValueError):
            relocate_material_reads(cb_ir(1, (2,)), old, new)

    def test_srv_count_limit_is_rejected_before_native_work(self):
        doc = self.service.doc
        operation = {'new_size': 16, 'textures_added': [f'Existing{i}' for i in range(254)],
                     'tables': [{'records': list(range(4)),
                                 'registers': {str(i): list(range(254)) for i in range(4)}}]}
        self.service.doc = Document(append_metadata(doc.original, doc.source, operation), doc.source)
        with patch('mmtr_editor.material_process.subprocess.run') as worker:
            with self.assertRaisesRegex(EditError, 'SRV数量'):
                self.plan([{'action': 'texture', 'name': 'BeyondLimit'}])
            worker.assert_not_called()

    def test_annotation_identity_is_local_to_function_and_unknown_is_rejected(self):
        header = '; UserMaterial cbuffer NA NA CB0 cb0 1\n; Other cbuffer NA NA CB1 cb1 1\n'

        def body(name, binding):
            return f'''define void @{name}() {{
  %1 = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind {{ i32 {binding}, i32 {binding}, i32 0, i8 2 }}, i32 {binding}, i1 false)
  %2 = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %1, %dx.types.ResourceProperties {{ i32 13, i32 32 }})
  ret void
}}
'''
        user, other = body('user', 0), body('other', 1)
        actual = patch_material_properties(header+user+other, 32, 48)
        self.assertIn(other, actual)
        self.assertIn('i32 13, i32 48', actual.split('define void @other')[0])
        with self.assertRaisesRegex(EditError, '资源身份'):
            patch_material_properties(user, 32, 48)


NATIVE_DXC = Path('C:/Software/Kiro/dxcompiler.dll')


# 真实 DXC 测试使用自有 HLSL 和合成 MMTR/MDF，所有原生工作通过隔离进程。
@unittest.skipUnless(NATIVE_DXC.is_file(), '可选 DXC 不可用，原生批次测试跳过')
class NativeMaterialBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        blob, _, _ = compile_hlsl(str(NATIVE_DXC), '''
cbuffer UserMaterial : register(b0) { float4 VAR_Value; };
Texture2D<float4> BaseMap : register(t3);
float4 main(float4 position: SV_Position) : SV_Target {
    return BaseMap.Load(int3(position.xy, 0)) * VAR_Value;
}
''', 'main', 'ps_6_6')
        prefix = bytearray(mmtr_fixture())
        start = struct.unpack_from('<Q', prefix, 8)[0]
        prefix = prefix[:start]
        cb = struct.unpack_from('<Q', prefix, 16+0x70)[0]
        members = struct.unpack_from('<Q', prefix, cb+24)[0]
        struct.pack_into('<H', prefix, members+14, 16)
        for i in range(4):
            struct.pack_into('<Q', prefix, 16+i*0x110+0x30, start+(i%2)*(len(blob)+8))
            struct.pack_into('<I', prefix, 16+i*0x110+0xCC, len(blob))
        cls.source = bytes(prefix)+(blob+struct.pack('<Q', xxh64(blob, 0xffffffff)))*2
        mdf = bytearray(mdf_fixture())
        mdf.extend(bytes(12))  # 合成 MDF 最后一块原本省略 12B；float4 需要完整值。
        struct.pack_into('<H', mdf, 280+20, 4)
        struct.pack_into('<H', mdf, 304+20, 4)
        cls.mdf_source = bytes(mdf)

    def service(self):
        service = Service()
        service.doc = Document(self.source, Path('MaterialShader/test.mmtr.240906211'))
        service.mdf = MdfDocument(self.mdf_source)
        service.mdf_selected = 0
        return service

    def apply(self, service, actions):
        return service.handle({'op': 'material_apply', 'actions': actions, 'record_index': 0, 'dll': str(NATIVE_DXC)})

    def test_all_widths_and_two_textures_compile_once_per_distinct_payload(self):
        service = self.service()
        original = MdfDocument(service.mdf.build()[0])
        actions = [{'action': 'parameter', 'name': f'Width{width}', 'byte_size': width,
                    'values': [float(width)]*(width//4)} for width in (4, 8, 12, 16)]
        actions.insert(1, {'action': 'texture', 'name': 'MapA'})
        actions.append({'action': 'texture', 'name': 'MapB'})
        result = self.apply(service, actions)
        self.assertEqual(result['target_materials'], [0, 1])
        stats = result['interface_sync']['statistics']
        self.assertEqual((stats['analyzed_shaders'], stats['assemble'], stats['validate'], stats['shader_cache_hits']), (1, 2, 1, 1))
        self.assertEqual(result['interface_sync']['changed_shaders'], 2)
        doc, mdf = Document(service.doc.build()[0], service.doc.source), MdfDocument(service.mdf.build()[0])
        self.assertFalse(doc.integrity['shader_trailer_mismatches'])
        cb = next(e for e in doc.info['metadata']['cbuffers'] if e['name'] == 'UserMaterial')
        self.assertEqual(cb['buffer_size'], 80)
        self.assertEqual([(m['byte_offset'], m['byte_size']) for m in cb['members'][1:]], [(16, 4), (32, 8), (48, 12), (64, 16)])
        for before, after in zip(original.materials, mdf.materials):
            from mmtr_editor.mdf import _public_data
            self.assertEqual(_public_data(before['properties']), _public_data(after['properties'][:1]))
            self.assertEqual(after['textures'][-2]['name'], 'MapA')
            self.assertEqual(after['textures'][-1]['name'], 'MapB')
            self.assertEqual(after['properties'][-1]['values'], [16.0]*4)

    def test_batch_and_sequential_have_identical_final_semantics(self):
        actions = [{'action': 'texture', 'name': 'MapA'}, {'action': 'parameter', 'name': 'P', 'byte_size': 8}]
        batch, sequential = self.service(), self.service()
        self.apply(batch, actions)
        for action in actions:
            self.apply(sequential, [action])
        from mmtr_editor.mdf import _public_data
        left, right = MdfDocument(batch.mdf.build()[0]), MdfDocument(sequential.mdf.build()[0])
        self.assertEqual([_public_data(m) for m in left.materials], [_public_data(m) for m in right.materials])
        for a, b in zip(batch.doc.current_info()['metadata']['cbuffers'], sequential.doc.current_info()['metadata']['cbuffers']):
            self.assertEqual([(m['name'], m['byte_offset'], m['byte_size']) for m in a['members']],
                             [(m['name'], m['byte_offset'], m['byte_size']) for m in b['members']])

    def test_payload_cache_does_not_cross_binding_context(self):
        source = bytearray(self.source)
        start = struct.unpack_from('<Q', source, 8)[0]
        for i in range(4):
            struct.pack_into('<Q', source, 16+i*0x110+0x30, start)
        service = self.service()
        service.doc = Document(source, service.doc.source)
        unused = service.doc.shader_bytes(1)
        result = self.apply(service, [{'action': 'texture', 'name': 'Map'}])
        self.assertEqual(result['interface_sync']['changed_shaders'], 1)
        self.assertEqual(result['interface_sync']['statistics']['shader_cache_hits'], 0)
        self.assertEqual(service.doc.shader_bytes(1), unused)

    def test_post_worker_failure_preserves_both_original_documents(self):
        service = self.service()
        before = service.doc.build()[0], service.mdf.build()[0]
        from mmtr_editor.material_service import current_layout

        def fail_candidate(doc, material):
            if doc.original != self.source:
                raise ValueError('injected final layout rejection')
            return current_layout(doc, material)

        with patch('mmtr_editor.material_service.current_layout', side_effect=fail_candidate):
            with self.assertRaisesRegex(ValueError, 'final layout rejection'):
                self.apply(service, [{'action': 'parameter', 'name': 'P', 'byte_size': 4}])
        self.assertEqual(before, (service.doc.build()[0], service.mdf.build()[0]))

    def test_changed_source_during_worker_is_preserved_and_result_rejected(self):
        service = self.service()
        before = service.doc.build()[0]
        from mmtr_editor.material_process import extend_in_worker

        def concurrent_edit(*args, **kwargs):
            result = extend_in_worker(*args, **kwargs)
            service.mdf.set_property(0, 'Value', [0.25]*4)
            return result

        with patch('mmtr_editor.material_service.extend_in_worker', side_effect=concurrent_edit):
            with self.assertRaisesRegex(EditError, '身份改变'):
                self.apply(service, [{'action': 'parameter', 'name': 'P', 'byte_size': 4}])
        self.assertEqual(service.doc.build()[0], before)
        self.assertEqual(service.mdf.material(0)['properties'][0]['values'], [0.25]*4)
        self.assertEqual(service.mdf.material(0)['property_count'], 1)

    def test_legacy_single_parameter_and_texture_paths_remain_compatible(self):
        service = self.service()
        result = service.handle({'op': 'mmtr_add_cbuffer_member', 'name': 'P', 'byte_size': 4,
                                 'byte_offset': 16, 'auto_sync': True, 'dll': str(NATIVE_DXC)})
        self.assertTrue(result['interface_sync']['validation']['valid'])
        for index in (0, 1):
            service.handle({'op': 'mdf_add_property', 'material_index': index, 'name': 'P', 'values': [0.0]})
        result = service.handle({'op': 'mmtr_add_resource', 'kind': 'srvs', 'name': 'Map',
                                 'record_index': 0, 'auto_sync': True, 'dll': str(NATIVE_DXC)})
        self.assertTrue(result['interface_sync']['validation']['valid'])
        for index in (0, 1):
            service.handle({'op': 'mdf_add_texture', 'material_index': index, 'name': 'Map',
                            'path': 'systems/rendering/NullBlack.tex'})
        self.assertEqual(service.mdf.material(1)['properties'][-1]['name'], 'P')
        self.assertEqual(service.mdf.material(1)['textures'][-1]['name'], 'Map')

    def test_other_cbuffer_size_is_not_changed_by_intermediate_alignment(self):
        _, _, ir = compile_hlsl(str(NATIVE_DXC), '''
cbuffer UserMaterial:register(b0){float4 VAR_Value;};
cbuffer Other:register(b1){float4 A;float4 B;};
float4 main(float4 p:SV_Position):SV_Target{return VAR_Value+A+B;}
''', 'main', 'ps_6_6')
        for name, old, new in [('P', 16, 32), ('Q', 32, 48)]:
            ir, _ = patch_declarations(ir, old, new,
                                       parameter={'name': name, 'byte_size': 4, 'byte_offset': old})
        definitions, _, _ = parse(ir)
        origins = handle_sources(ir, definitions)
        annotations = {origins[value]['binding']['name']: row['text'] for value, row in definitions.items()
                       if 'annotateHandle' in row['text'] and value in origins}
        self.assertIn('i32 13, i32 32', annotations['Other'])
        self.assertIn('i32 13, i32 48', annotations['UserMaterial'])

    # PR #3 放宽的寻址形状必须走真正的混合批次和 DXC，不能只检查合成文本替换。
    def test_instance_shift_and_constant_address_survive_batch_compilation(self):
        from mmtr_editor.dxc_process import disassemble_shader
        for address in ('uint(position.x) * 16', '0'):
            with self.subTest(address=address):
                blob, _, before_ir = compile_hlsl(str(NATIVE_DXC), '''
cbuffer UserMaterial : register(b0) { float4 VAR_Value; };
Texture2D<float4> BaseMap : register(t3);
ByteAddressBuffer UserMaterialInstances : register(t4);
float4 main(float4 position: SV_Position) : SV_Target {
    return BaseMap.Load(int3(position.xy, 0)) * VAR_Value
         + asfloat(UserMaterialInstances.Load4('''+address+'''));
}
''', 'main', 'ps_6_6')
                prefix = bytearray(self.source[:struct.unpack_from('<Q', self.source, 8)[0]])
                for i in range(4):
                    struct.pack_into('<Q', prefix, 16+i*0x110+0x30, len(prefix)+(i%2)*(len(blob)+8))
                    struct.pack_into('<I', prefix, 16+i*0x110+0xCC, len(blob))
                service = self.service()
                service.doc = Document(bytes(prefix)+(blob+struct.pack('<Q', xxh64(blob, 0xffffffff)))*2,
                                       service.doc.source)
                before = [r for r in material_reads(before_ir) if r['kind'] == 'instance']
                self.assertTrue(before)
                if address == '0':
                    self.assertTrue(all(r['stride'] is None for r in before))
                else:
                    self.assertTrue(all(r['stride']['op'] == 'shl' for r in before))
                result = self.apply(service, [{'action': 'parameter', 'name': 'P', 'byte_size': 4},
                                              {'action': 'texture', 'name': 'Map'}])
                self.assertTrue(result['interface_sync']['validation']['valid'])
                after_ir = disassemble_shader(str(NATIVE_DXC), service.doc.shader_bytes(0))
                after = [r for r in material_reads(after_ir) if r['kind'] == 'instance']
                self.assertEqual([[e['offset'] for e in r['extracts']] for r in before],
                                 [[e['offset'] for e in r['extracts']] for r in after])
                for read in after:
                    self.assertEqual(read['stride']['bytes'] if read['stride'] else None,
                                     None if address == '0' else 32)


if __name__ == '__main__':
    unittest.main()
