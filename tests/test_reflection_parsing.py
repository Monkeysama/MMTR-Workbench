"""DXC 反射文本解析；覆盖长字段名后的双分号格式。"""
import unittest

from inspect_mmtr import reflection


class ReflectionParsingTests(unittest.TestCase):
    def test_double_semicolon_after_variable_is_retained(self):
        text = """; Buffer Definitions:
;
; cbuffer UserMaterial
; {
;
;   struct UserMaterial
;   {
;
;       float4 VAR_Hair_Color;                        ; Offset:    0
;       float4 VAR_Player_hair_specular_Color_primary;; Offset:   48
;       float VAR_Nuki;                               ; Offset:   64
;
;   } UserMaterial;                                   ; Offset:    0 Size:   112
;
; }
;
; Resource Bindings:
"""
        parsed = reflection(text)
        self.assertEqual(parsed['cbuffers'], [{
            'name': 'UserMaterial',
            'size': 112,
            'variables': [
                {'type': 'float4', 'name': 'VAR_Hair_Color', 'offset': 0},
                {'type': 'float4', 'name': 'VAR_Player_hair_specular_Color_primary', 'offset': 48},
                {'type': 'float', 'name': 'VAR_Nuki', 'offset': 64},
            ],
        }])


if __name__ == '__main__':
    unittest.main()
