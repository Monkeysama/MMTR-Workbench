"""动态纹理的 CPU 来源证明及隔离 DXC 事务；合成语料不声称游戏效果。"""
from pathlib import Path
import re
import struct
import unittest

from inspect_mmtr import reflection
from mmtr_editor.dxc_process import compile_hlsl, disassemble_shader
from mmtr_editor.hashes import murmur3_x86_32, xxh64
from mmtr_editor.material_addresses import MaterialTailLayout, material_reads
from mmtr_editor.material_markers import material_marker_reads
from mmtr_editor.material_textures import native_texture_reads
from mmtr_editor.mdf import MdfDocument
from mmtr_editor.model import Document, EditError
from mmtr_editor.rpc import Service
from test_mdf_editing import fixture as mdf_fixture
from test_structure import fixture as mmtr_fixture

DXC = Path('C:/Software/Kiro/dxcompiler.dll')
LAYOUT = MaterialTailLayout(16, ('BaseMap',), ())
NAMED = '''
cbuffer UserMaterial:register(b0){float4 VAR_Value;};
StructuredBuffer<uint4> BindlessBuffer:register(t3);
Texture2D<float4> bindlessTexture2D[]:register(t0,space7);
float4 main(float4 p:SV_Position):SV_Target {
    uint4 slots=BindlessBuffer[(asuint(VAR_Value.x)>>4)+1];
    return bindlessTexture2D[NonUniformResourceIndex(slots.x)].Load(int3(p.xy,0))+VAR_Value;
}
'''
HEAP = '''
cbuffer UserMaterial:register(b0){float4 VAR_Value;};
struct Material {uint4 words[2048];};
float4 main(float4 p:SV_Position):SV_Target {
    ConstantBuffer<Material> material=ResourceDescriptorHeap[asuint(p.z)];
    Texture2D<float4> texture=ResourceDescriptorHeap[NonUniformResourceIndex(material.words[1].x)];
    return texture.Load(int3(p.xy,0))+VAR_Value;
}
'''
FIXED = '''
cbuffer UserMaterial:register(b0){float4 VAR_Value;};
Texture2D<float4> BaseMap:register(t3);
float4 main(float4 p:SV_Position):SV_Target {return BaseMap.Load(int3(p.xy,0))+VAR_Value;}
'''


# 三种构造共享五组记录，metadata 的引擎 SRV 绑定保持不变。
def source_with(blobs, fixed_index=None):
    data = bytearray(mmtr_fixture(5, len(blobs)))
    start = struct.unpack_from('<Q', data, 8)[0]
    data = data[:start]
    name_at = len(data)
    data.extend(b'EngineInput\0')
    srv = struct.unpack_from('<Q', data, 16+0x90)[0]
    fixed_srv = bytes(data[srv:srv+16])
    struct.pack_into('<QI', data, srv, name_at, murmur3_x86_32(b'EngineInput', 0xffffffff))
    fixed_at = len(data)
    if fixed_index is not None:
        data.extend(fixed_srv)
    cb = struct.unpack_from('<Q', data, 16+0x70)[0]
    members = struct.unpack_from('<Q', data, cb+24)[0]
    struct.pack_into('<H', data, members+14, 16)
    start = len(data)
    struct.pack_into('<Q', data, 8, start)
    offsets = []
    for blob in blobs:
        offsets.append(len(data))
        data.extend(blob+struct.pack('<Q', xxh64(blob, 0xffffffff)))
    for i in range(5*len(blobs)):
        base = 16+i*0x110
        if i % len(blobs) == fixed_index:
            struct.pack_into('<Q', data, base+0x90, fixed_at)
        struct.pack_into('<Q', data, base+0x30, offsets[i%len(blobs)])
        struct.pack_into('<I', data, base+0xcc, len(blobs[i%len(blobs)]))
    return bytes(data)


@unittest.skipUnless(DXC.is_file(), '本地原生 DXC 不可用')
class NativeTextureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiled = [compile_hlsl(str(DXC), code, 'main', profile)
                        for code, profile in ((NAMED, 'ps_6_0'), (NAMED, 'ps_6_6'), (HEAP, 'ps_6_6'))]
        cls.source = source_with([row[0] for row in cls.compiled])
        mdf = bytearray(mdf_fixture())
        mdf.extend(bytes(12))
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
        return service.handle({'op': 'material_apply', 'actions': actions, 'record_index': 0, 'dll': str(DXC)})

    def test_legacy_modern_and_heap_prove_actual_mdf_slot(self):
        for _, _, ir in self.compiled:
            with self.subTest():
                proof = native_texture_reads(ir, LAYOUT, material_reads(ir), reflection(ir))
                self.assertEqual([p['texture'] for p in proof], ['BaseMap'])
        self.assertIn('createHandle(i32 57', self.compiled[0][2])
        self.assertIn('createHandleFromBinding(i32 217', self.compiled[1][2])
        self.assertIn('createHandleFromHeap(i32 218', self.compiled[2][2])

    def test_texture_only_preserves_entire_native_mmtr_and_syncs_both_materials(self):
        service = self.service()
        result = self.apply(service, [{'action': 'texture', 'name': 'MapA'}, {'action': 'texture', 'name': 'MapB'}])
        report = result['interface_sync']
        self.assertEqual((report['srv_records'], report['native_texture_shaders'], report['changed_shaders']), (0, 3, 0))
        self.assertEqual(service.doc.build()[0], self.source)
        for material in MdfDocument(service.mdf.build()[0]).materials:
            self.assertEqual([t['name'] for t in material['textures']], ['BaseMap', 'MapA', 'MapB'])

    def test_parameter_and_continuous_texture_migrate_identity_without_new_srv(self):
        service = self.service()
        before = service.doc.info
        self.apply(service, [{'action': 'parameter', 'name': 'P', 'byte_size': 4},
                             {'action': 'texture', 'name': 'MapA'}])
        result = self.apply(service, [{'action': 'parameter', 'name': 'Q', 'byte_size': 8},
                                      {'action': 'texture', 'name': 'MapB'}])
        self.assertEqual(result['interface_sync']['native_texture_shaders'], 3)
        rebuilt = service.doc.build()[0]
        reopened = Document(rebuilt, service.doc.source)
        self.assertEqual(reopened.info['header_u16_04'], 5)
        self.assertFalse(reopened.integrity['name_hash_mismatches'])
        self.assertFalse(reopened.integrity['shader_trailer_mismatches'])
        layout = MaterialTailLayout(48, ('BaseMap', 'MapA', 'MapB'), ())
        for shader in reopened.info['shaders']:
            ir = disassemble_shader(str(DXC), reopened.shader_bytes(shader['index']))
            self.assertEqual([p['texture'] for p in native_texture_reads(ir, layout, material_reads(ir), reflection(ir))], ['BaseMap'])
            self.assertFalse(any(row.split()[0] in ('MapA', 'MapB') for row in reflection(ir)['resource_binding_rows']))
        for old, new in zip(before['records'], reopened.info['records']):
            base = new['offset']
            self.assertEqual(rebuilt[base+0x40:base+0xc0], self.source[base+0x40:base+0xc0])
            self.assertEqual(rebuilt[base+0xd4:base+0x110], self.source[base+0xd4:base+0x110])
            self.assertEqual(old['metadata_tables']['srvs'], new['metadata_tables']['srvs'])

    def test_unknown_index_parameter_slot_and_mixed_paths_roll_back(self):
        invalid = [NAMED.replace('slots.x)', '(slots.x+1))'),
                   NAMED.replace('+1]', '+0]'),
                   NAMED.replace('float4 main', 'Texture2D<float4> BaseMap:register(t4);\nfloat4 main')
                        .replace('+VAR_Value;', '+VAR_Value+BaseMap.Load(int3(p.xy,0));')]
        for code in invalid:
            with self.subTest(code=code):
                blob, _, _ = compile_hlsl(str(DXC), code, 'main', 'ps_6_0')
                service = self.service()
                service.doc = Document(source_with([blob]), service.doc.source)
                before = service.doc.build()[0], service.mdf.build()[0]
                with self.assertRaises(EditError):
                    self.apply(service, [{'action': 'texture', 'name': 'Rejected'}])
                self.assertEqual(before, (service.doc.build()[0], service.mdf.build()[0]))

    def test_unbounded_declaration_shape_and_binding_are_checked(self):
        ir = self.compiled[0][2]
        wrong, count = re.subn(r'(!"(?:bindlessTexture2D)?", i32 7, i32 0, i32 )-1', r'\g<1>8', ir)
        self.assertEqual(count, 1)
        with self.assertRaisesRegex(EditError, '无界'):
            native_texture_reads(wrong, LAYOUT, material_reads(wrong), reflection(wrong))

    def test_one_master_with_fixed_and_native_programs_uses_separate_strategies(self):
        fixed, _, _ = compile_hlsl(str(DXC), FIXED, 'main', 'ps_6_6')
        data = source_with([row[0] for row in self.compiled]+[fixed], fixed_index=3)
        service = self.service()
        service.doc = Document(data, service.doc.source)
        result = self.apply(service, [{'action': 'texture', 'name': 'Added'}])
        report = result['interface_sync']
        self.assertEqual((report['native_texture_shaders'], report['srv_records']), (3, 5))
        output = service.doc.build()[0]
        reopened = Document(output, service.doc.source)
        for index in range(3):
            self.assertEqual(reopened.shader_bytes(index), self.compiled[index][0])
        ir = disassemble_shader(str(DXC), reopened.shader_bytes(3))
        self.assertEqual(sum(row.split()[0] == 'Added' for row in reflection(ir)['resource_binding_rows']), 1)
        for record in reopened.info['records']:
            if record['index'] % 4 != 3:
                base = record['offset']
                self.assertEqual(output[base+0x40:base+0xc0], data[base+0x40:base+0xc0])
                self.assertEqual(output[base+0xd4:base+0x110], data[base+0xd4:base+0x110])

    def test_native_shader_shared_by_another_stage_is_rejected(self):
        service = self.service()
        data = bytearray(self.source)
        struct.pack_into('<Q', data, 16+0x10, struct.unpack_from('<Q', data, 16+0x30)[0])
        struct.pack_into('<I', data, 16+0xbc, len(self.compiled[0][0]))
        service.doc = Document(data, service.doc.source)
        before = service.doc.build()[0], service.mdf.build()[0]
        with self.assertRaisesRegex(EditError, '其它阶段'):
            self.apply(service, [{'action': 'texture', 'name': 'Rejected'}])
        self.assertEqual(before, (service.doc.build()[0], service.mdf.build()[0]))

    def test_native_layout_marker_migrates_address_and_counts_without_bypass(self):
        named = NAMED.replace('    return bindlessTexture2D',
            '    if (BindlessBuffer[(asuint(VAR_Value.x)>>4)+2].x != 1028) return 0;\n    return bindlessTexture2D')
        heap = HEAP.replace('    return texture.Load',
            '    if (material.words[2].x != 1028) return 0;\n    return texture.Load')
        blobs = [compile_hlsl(str(DXC), code, 'main', profile)[0]
                 for code, profile in ((named, 'ps_6_0'), (named, 'ps_6_6'), (heap, 'ps_6_6'))]
        service = self.service()
        service.doc = Document(source_with(blobs), service.doc.source)
        # 只加纹理时对齐位置不变，但标记值必须改变；随后参数扩容跨越16B边界。
        for actions, layout, components in (
                ([{'action': 'texture', 'name': 'MapA'}], MaterialTailLayout(16, ('BaseMap', 'MapA'), ()), 4),
                ([{'action': 'parameter', 'name': 'P', 'byte_size': 4}, {'action': 'texture', 'name': 'MapB'}],
                 MaterialTailLayout(32, ('BaseMap', 'MapA', 'MapB'), ()), 5)):
            result = self.apply(service, actions)
            self.assertEqual(sum(r['material_markers'] for r in result['interface_sync']['shader_reports']), 3)
            reopened = Document(service.doc.build()[0], service.doc.source)
            for shader in reopened.info['shaders']:
                ir = disassemble_shader(str(DXC), reopened.shader_bytes(shader['index']))
                _, guards = material_marker_reads(ir, layout, components, material_reads(ir))
                self.assertEqual(len(guards), 1)
                self.assertEqual([p['texture'] for p in native_texture_reads(ir, layout, material_reads(ir), reflection(ir))], ['BaseMap'])

    def test_marker_wrong_count_position_and_extra_consumer_roll_back(self):
        guarded = NAMED.replace('    return bindlessTexture2D',
            '    uint marker=BindlessBuffer[(asuint(VAR_Value.x)>>4)+2].x;\n'
            '    if (marker != 1028) return 0;\n    return bindlessTexture2D')
        for code in (guarded.replace('!= 1028', '!= 1027'), guarded.replace('+2].x', '+3].x')):
            with self.subTest(code=code):
                blob, _, _ = compile_hlsl(str(DXC), code, 'main', 'ps_6_0')
                service = self.service()
                service.doc = Document(source_with([blob]), service.doc.source)
                before = service.doc.build()[0], service.mdf.build()[0]
                with self.assertRaises(EditError):
                    self.apply(service, [{'action': 'texture', 'name': 'Rejected'}])
                self.assertEqual(before, (service.doc.build()[0], service.mdf.build()[0]))
        # DXC 会把分支后已知等于常量的 marker 折叠；直接在 IR 注入额外使用者验证拒绝条件。
        _, _, ir = compile_hlsl(str(DXC), guarded, 'main', 'ps_6_0')
        _, guards = material_marker_reads(ir, LAYOUT, 4, material_reads(ir))
        ir = ir.replace('  ret void', f'  %extraMarkerUse = icmp ne i32 {guards[0]["extract"]}, 1\n  ret void')
        with self.assertRaisesRegex(EditError, '消费者'):
            material_marker_reads(ir, LAYOUT, 4, material_reads(ir))
