"""混合space的SRV追加语义：保留无界声明、正确分配有限寄存器并拒绝不明范围。"""
import unittest

from mmtr_editor.material_extension import patch_declarations
from mmtr_editor.model import EditError


# 合成资源表不含游戏资产；无界数组在前，防止误把数组类型用作单纹理模板。
def resource_ir(count=-1, space=4, extra=''):
    return '''!dx.resources = !{!0}
!0 = !{!1, null, null, null}
!1 = !{!2, !3, !4'''+(', !5' if extra else '')+'''}
!2 = !{i32 8, [0 x %"class.Texture2D<vector<float, 4> >"]* undef, !"bindless", i32 '''+str(space)+''', i32 0, i32 '''+str(count)+''', i32 2, i32 0, !6}
!3 = !{i32 2, %"class.Texture2D<vector<float, 4> >"* undef, !"Base", i32 0, i32 3, i32 1, i32 2, i32 0, !6}
!4 = !{i32 4, [2 x %struct.ByteAddressBuffer]* undef, !"Raw", i32 0, i32 5, i32 2, i32 11, i32 0, null}
!6 = !{i32 0, i32 9}
'''+('!5 = '+extra+'\n' if extra else '')


class TextureDeclarationsTests(unittest.TestCase):
    def test_mixed_unbounded_spaces_keep_nodes_and_allocate_only_space0(self):
        for count in (-1, 4294967295, 100000):
            with self.subTest(count=count):
                original = resource_ir(count=count)
                actual, register = patch_declarations(original, 944, 944, texture='NewMap')
                self.assertEqual(register, 7)
                for line in original.splitlines():
                    if line.startswith(('!2 =', '!3 =', '!4 =', '!6 =')):
                        self.assertIn(line, actual)
                added = next(line for line in actual.splitlines() if '!"NewMap"' in line)
                self.assertIn('i32 9, %"class.Texture2D<vector<float, 4> >"* undef', added)
                self.assertIn('!"NewMap", i32 0, i32 7, i32 1, i32 2, i32 0, !6}', added)

    def test_finite_space0_only_keeps_previous_behavior(self):
        actual, register = patch_declarations(resource_ir(count=1, space=0), 944, 944, texture='NewMap')
        self.assertEqual(register, 7)
        self.assertIn('!"NewMap"', actual)

    def test_space0_unbounded_and_unknown_counts_are_rejected(self):
        for count in (-1, 4294967295):
            with self.subTest(count=count), self.assertRaisesRegex(EditError, 'space0.*无界'):
                patch_declarations(resource_ir(count=count, space=0), 944, 944, texture='NewMap')
        for count in (-2, 0, 4294967296):
            with self.subTest(count=count), self.assertRaisesRegex(EditError, '数量'):
                patch_declarations(resource_ir(count=count), 944, 944, texture='NewMap')

    def test_other_space_scalar_is_not_used_as_template(self):
        ir = resource_ir().replace('!"Base", i32 0,', '!"Base", i32 2,')
        with self.assertRaisesRegex(EditError, '单个float4'):
            patch_declarations(ir, 944, 944, texture='NewMap')

    def test_overlapping_or_exhausted_registers_are_rejected(self):
        extra = '!{i32 7, %struct.ByteAddressBuffer* undef, !"Overlap", i32 0, i32 6, i32 1, i32 11, i32 0, null}'
        with self.assertRaisesRegex(EditError, '重叠'):
            patch_declarations(resource_ir(extra=extra), 944, 944, texture='NewMap')
        with self.assertRaisesRegex(EditError, '超出'):
            patch_declarations(resource_ir().replace('i32 5, i32 2, i32 11', 'i32 65535, i32 1, i32 11'),
                               944, 944, texture='NewMap')

    def test_duplicate_identity_and_unrecognized_declaration_are_rejected(self):
        with self.assertRaisesRegex(EditError, '身份'):
            patch_declarations(resource_ir().replace('i32 8, [0 x', 'i32 2, [0 x'), 944, 944, texture='NewMap')
        with self.assertRaisesRegex(EditError, '结构'):
            patch_declarations(resource_ir().replace('i32 -1, i32 2', 'i64 -1, i32 2'), 944, 944, texture='NewMap')
