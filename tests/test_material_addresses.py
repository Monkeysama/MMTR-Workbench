"""材质尾部迁移语义测试：按字段标记字验证读取，保留错误候选作为阴性对照。"""
from pathlib import Path
import json
import re
import struct
import unittest

from mmtr_editor.material_addresses import MaterialTailLayout, material_reads, relocate_material_reads

ROOT = Path(__file__).resolve().parents[1]
OLD = MaterialTailLayout(960, tuple(f'tex{i}' for i in range(22)), ('dynamic', 'foliageStatic', 'foliageDynamic'))
NEW = MaterialTailLayout(976, OLD.texture_names + ('newTex',), OLD.gpbf_names)


# 合成CBV只暴露纹理/GPBF边界，不依赖游戏资产；返回值SSA由调用者做独立字段对照。
def cb_ir(register, lanes):
    return '''%dx.types.Handle = type { i8* }
define void @main() {
  %heap = call %dx.types.Handle @dx.op.createHandleFromHeap(i32 218, i32 %descriptor, i1 false, i1 false)
  %cb = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %heap, %dx.types.ResourceProperties { i32 13, i32 32768 })
  %load = call %dx.types.CBufRet.i32 @dx.op.cbufferLoadLegacy.i32(i32 59, %dx.types.Handle %cb, i32 ''' + str(register) + ''')
''' + ''.join(f'  %field{lane} = extractvalue %dx.types.CBufRet.i32 %load, {lane}\n' for lane in lanes) + '  ret void\n}\n'


# 使用互不相同的值标记参数和资源；旧SSA在新布局读错任何字段都会失败。
def marked_bytes(layout):
    data = bytearray(layout.parameter_bytes + 4 * (len(layout.texture_names) + len(layout.gpbf_names)))
    for offset in range(0, 960, 4):
        struct.pack_into('<I', data, offset, 0x10000000 + offset)
    for index, name in enumerate(OLD.texture_names + OLD.gpbf_names):
        struct.pack_into('<I', data, layout.fields()[name], 0x20000000 + index)
    return data


class MaterialAddressesTests(unittest.TestCase):
    # 自定义效果会直接用f32读取heap材质CB；扩容后参数不动、资源逐字段迁移。
    def test_float_cb_reads_preserve_parameters_and_relocate_tail(self):
        for register, lanes in [(59, range(4)), (60, range(4)), (65, range(4)), (66, [0])]:
            original = cb_ir(register, lanes).replace('CBufRet.i32', 'CBufRet.f32').replace(
                'cbufferLoadLegacy.i32', 'cbufferLoadLegacy.f32')
            with self.subTest(register=register):
                actual, _ = relocate_material_reads(original, OLD, NEW)
                before = {ex['ssa']: ex['offset'] for r in material_reads(original) for ex in r['extracts']}
                after = {ex['ssa']: ex['offset'] for r in material_reads(actual) for ex in r['extracts']}
                for ssa, offset in before.items():
                    self.assertEqual(after[ssa], OLD.offset_in(offset, NEW))
                    self.assertEqual(marked_bytes(OLD)[offset:offset+4],
                                     marked_bytes(NEW)[after[ssa]:after[ssa]+4])
                self.assertNotIn('CBufRet.i32', actual)
                self.assertNotIn('cbufferLoadLegacy.i32', actual)
                if register == 59:
                    self.assertEqual(actual, original)

    def test_float_cb_unknown_tail_and_aggregate_consumer_still_rejected(self):
        original = cb_ir(66, [1]).replace('CBufRet.i32', 'CBufRet.f32').replace(
            'cbufferLoadLegacy.i32', 'cbufferLoadLegacy.f32')
        with self.assertRaisesRegex(ValueError, '资源身份'):
            relocate_material_reads(original, OLD, NEW)
        original = cb_ir(60, [0]).replace('CBufRet.i32', 'CBufRet.f32').replace(
            'cbufferLoadLegacy.i32', 'cbufferLoadLegacy.f32').replace(
                '  ret void', '  call void @escape(%dx.types.CBufRet.f32 %load)\n  ret void')
        with self.assertRaisesRegex(ValueError, 'aggregate'):
            relocate_material_reads(original, OLD, NEW)

    # 模拟已有fresnel参数的材质连续扩展，避免把有效索引留在旧参数地址。
    def test_custom_material_three_textures_and_nine_parameters(self):
        old = MaterialTailLayout(1024, tuple(f'tex{i}' for i in range(21)), ('gp0', 'gp1', 'gp2', 'gp3'))
        original = cb_ir(64, range(4))
        float_load = cb_ir(59, [0]).replace('CBufRet.i32', 'CBufRet.f32').replace(
            'cbufferLoadLegacy.i32', 'cbufferLoadLegacy.f32')
        for initial in (original, float_load, cb_ir(69, range(4)), cb_ir(70, [0])):
            before = {ex['ssa']: ex['offset'] for r in material_reads(initial) for ex in r['extracts']}
            actual, current = initial, old
            for i in range(3):
                target = MaterialTailLayout(1024, current.texture_names + (f'newTex{i}',), current.gpbf_names)
                actual, _ = relocate_material_reads(actual, current, target)
                current = target
            for _ in range(9):
                target = MaterialTailLayout(current.parameter_bytes + 16, current.texture_names, current.gpbf_names)
                actual, _ = relocate_material_reads(actual, current, target)
                current = target
            for read in material_reads(actual):
                for ex in read['extracts']:
                    offset = before[ex['ssa']]
                    expected = offset if offset < 1024 else offset + 144 + (12 if offset >= 1108 else 0)
                    self.assertEqual(ex['offset'], expected)

    def test_texture_then_parameter_extension_allocates_distinct_ssa_names(self):
        old = MaterialTailLayout(944, tuple(f'tex{i}' for i in range(21)), ('gp0', 'gp1', 'gp2'))
        middle = MaterialTailLayout(944, old.texture_names + ('newTex',), old.gpbf_names)
        final = MaterialTailLayout(960, middle.texture_names, old.gpbf_names)
        raw = '''define void @main() {
  %raw = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 0, i32 0, i32 0, i1 false)
  %base = lshr i32 %material, 4
  %addr = add i32 %base, 64
  %load = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %raw, i32 %addr, i32 0, i8 15, i32 4)
''' + ''.join(f'  %field{lane} = extractvalue %dx.types.ResRet.i32 %load, {lane}\n' for lane in range(4)) + '''  ret void
}
; BindlessBuffer texture byte r/o T0 t0 1
'''
        for original in (cb_ir(64, range(4)), raw):
            with self.subTest(kind='raw' if original == raw else 'cb'):
                first, _ = relocate_material_reads(original, old, middle)
                actual, _ = relocate_material_reads(first, middle, final)
                names = re.findall(r'^\s*(%\w+)\s*=', actual, re.M)
                self.assertEqual(len(names), len(set(names)))
                after = {ex['ssa']: ex['offset'] for r in material_reads(actual) for ex in r['extracts']}
                for lane in range(4):
                    expected = old.offset_in(1024 + lane * 4, final)
                    self.assertEqual(after[f'%field{lane}'], expected)

    def test_texture_gpbf_boundary_reads_preserve_identity(self):
        old_mem, new_mem = marked_bytes(OLD), marked_bytes(NEW)
        for register, lanes in [(59, range(4)), (60, range(4)), (65, range(4)), (66, [0])]:
            original = cb_ir(register, lanes)
            actual, _ = relocate_material_reads(original, OLD, NEW)
            before = {ex['ssa']: ex['offset'] for r in material_reads(original) for ex in r['extracts']}
            after = {ex['ssa']: ex['offset'] for r in material_reads(actual) for ex in r['extracts']}
            for ssa, offset in before.items():
                self.assertEqual(old_mem[offset:offset+4], new_mem[after[ssa]:after[ssa]+4])

    def test_old_code_reading_new_parameters_fails_semantic_control(self):
        old_mem, new_mem = marked_bytes(OLD), marked_bytes(NEW)
        self.assertNotEqual(old_mem[964:968], new_mem[964:968])
        self.assertNotEqual(old_mem[1048:1052], new_mem[1048:1052])

    def test_unknown_tail_or_aggregate_consumer_rejected(self):
        with self.assertRaisesRegex(ValueError, '资源身份'):
            relocate_material_reads(cb_ir(66, [1]), OLD, NEW)
        aggregate = cb_ir(60, [0]).replace('  ret void', '  call void @escape(%dx.types.CBufRet.i32 %load)\n  ret void')
        with self.assertRaisesRegex(ValueError, 'aggregate'):
            relocate_material_reads(aggregate, OLD, NEW)
        with self.assertRaisesRegex(ValueError, '32768B'):
            relocate_material_reads(cb_ir(60, [0]).replace('i32 32768', 'i32 16384'), OLD, NEW)

    def test_instance_stride_change_replaces_alignment_dependent_or(self):
        ir = '''define void @main() {
  %raw = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 0, i32 0, i32 0, i1 false)
  %base = mul i32 %instance, 960
  %addr = or i32 %base, 16
  %load = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %raw, i32 %addr, i32 undef, i8 15, i32 4)
  %value = extractvalue %dx.types.ResRet.i32 %load, 0
  ret void
}
; UserMaterialInstances texture byte r/o T0 t0 1
'''
        updated, _ = relocate_material_reads(ir, OLD, NEW)
        self.assertIn('%base = mul i32 %instance, 976', updated)
        self.assertIn('%addr = add i32 %base, 16', updated)
        self.assertNotEqual(976 | 16, 976 + 16)
        for index in (0,1,2,3,17):
            self.assertEqual(index*960 | 16,index*960 + 16)
            self.assertEqual((index*976 + 16) - index*976,16)
        with self.assertRaisesRegex(ValueError,'OR地址'):
            relocate_material_reads(ir.replace('%base, 16','%base, 64'),OLD,NEW)


@unittest.skipUnless((ROOT/'output/pants-interface-test-v2-20261019/manifest.json').is_file(), 'Pants v2 local candidate unavailable')
class PantsCandidateAddressesTests(unittest.TestCase):
    def test_every_original_scalar_reads_same_field_in_compiled_candidate(self):
        from mmtr_editor.model import Document
        from mmtr_editor.dxc import Dxc
        from mmtr_editor.mdf import MdfDocument
        out = ROOT/'output/pants-interface-test-v2-20261019'
        manifest = json.loads((out/'manifest.json').read_text(encoding='utf-8'))
        original = Document.open(manifest['source_mmtr'])
        candidate = Document.open(out/'candidate'/manifest['files'][0]['relative'])
        material = MdfDocument.open(out/'backup/original.mdf2.45').material(5)
        textures = tuple(t['name'] for t in material['textures'])
        gpbf = tuple(g['name'] for g in material['gpbf'])
        old, new = MaterialTailLayout(960, textures, gpbf), MaterialTailLayout(976, textures+('TestTexture',), gpbf)
        old_mem, new_mem = bytearray(1060), bytearray(1080)
        for offset in range(0,960,4):
            struct.pack_into('<I',old_mem,offset,0x10000000+offset)
            struct.pack_into('<I',new_mem,offset,0x10000000+offset)
        for index,name in enumerate(textures+gpbf):
            struct.pack_into('<I',old_mem,old.fields()[name],0x20000000+index)
            struct.pack_into('<I',new_mem,new.fields()[name],0x20000000+index)
        count = 0
        compiler = Dxc('C:/Software/Kiro/dxcompiler.dll')
        try:
            for shader in original.info['shaders']:
                index = shader['index']
                old_ir=compiler.disassemble(original.shader_bytes(index))
                new_ir=compiler.disassemble(candidate.shader_bytes(index))
                old_reads, new_reads = material_reads(old_ir), material_reads(new_ir)
                after={ex['ssa']:(r,ex) for r in new_reads for ex in r['extracts']}
                for read in old_reads:
                    for ex in read['extracts']:
                        updated, actual = after[ex['ssa']]
                        self.assertEqual(old_mem[ex['offset']:ex['offset']+4],new_mem[actual['offset']:actual['offset']+4],(index,ex))
                        if read['kind']=='instance':
                            self.assertEqual(updated['stride']['bytes'],976)
                            self.assertEqual(ex['offset'],actual['offset'])
                        count += 1
        finally:
            compiler.close()
        self.assertGreater(count, 10000)
        print(f'Compared {count} material scalar reads across all 557 compiled programs.')


if __name__ == '__main__':
    unittest.main()
