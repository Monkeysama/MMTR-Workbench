"""MDF 2.51 两种材质头的识别与原样往返。"""
from pathlib import Path
import struct
import unittest

from mmtr_editor.mdf import MdfDocument, MdfError
from test_onimusha_support import mdf51_fixture


SOURCE = Path('fixture.mdf2.51')


def standard_fixture(count=2):
    size = 108
    texture_base = (16 + count * size + 7) // 8 * 8
    property_base = texture_base + count * 32
    data_base = property_base + count * 24
    data = bytearray(data_base + count * 16)
    data[:8] = b'MDF\0\x01\0' + struct.pack('<H', count)

    def string(value):
        result = len(data)
        data.extend(value.encode('utf-16-le') + b'\0\0')
        return result

    slot, prop, texture, master = (string(x) for x in
        ('BaseMap', 'Value', 'Textures/base.tex', 'MaterialShader/test.mmtr'))
    for i in range(count):
        base = 16 + i * size
        struct.pack_into('<Q', data, base, string(f'Material{i}'))
        struct.pack_into('<IIII', data, base + 8, 0x1234 + i, 16, 1, 1)
        struct.pack_into('<i', data, base + 36, 1)
        data[base + 48:base + 56] = b'\x71\x72\x73\x74\x75\x76\x77\x78'
        struct.pack_into('<6Q', data, base + 60, property_base + i * 24,
                         texture_base + i * 32, 0, data_base + i * 16, master, 0)
        struct.pack_into('<QIIQII', data, texture_base + i * 32, slot, 1, 2, texture, 3, 4)
        struct.pack_into('<QIIIHH', data, property_base + i * 24, prop, 5, 6, 0, 1, 0x1234 + i)
        struct.pack_into('<f', data, data_base + i * 16, 0.5 + i)
    return bytes(data)


class Mdf51LayoutTests(unittest.TestCase):
    def test_both_layouts_detect_and_survive_consecutive_structural_edits(self):
        for original, size in ((mdf51_fixture(), 104), (standard_fixture(), 108)):
            with self.subTest(size=size):
                doc = MdfDocument(original, SOURCE)
                self.assertEqual(doc.MATERIAL_SIZE, size)
                self.assertEqual(doc.game, 'OnimushaWotS' if size == 104 else 'Unknown')
                self.assertEqual(doc.build()[0], original)
                before = doc.material(1)
                doc.add_texture(0, 'ExtraMap', 'Textures/extra.tex')
                doc.add_property(0, 'Extra', [2.0], offset=4)
                first, _ = doc.build()
                self.assertEqual(first[16 + size:16 + 2 * size], original[16 + size:16 + 2 * size])
                if size == 108:
                    self.assertEqual(first[16 + 48:16 + 56], original[16 + 48:16 + 56])
                reopened = MdfDocument(first, SOURCE)
                self.assertEqual(reopened.MATERIAL_SIZE, size)
                self.assertEqual(reopened.material(1), before)
                reopened.add_texture(1, 'SecondMap', 'Textures/second.tex')
                second, _ = reopened.build()
                final = MdfDocument(second, SOURCE)
                self.assertEqual(final.MATERIAL_SIZE, size)
                self.assertEqual(final.material(1)['textures'][-1]['name'], 'SecondMap')

    def test_one_material_standard_and_missing_last_padding(self):
        original = bytearray(standard_fixture(1))
        struct.pack_into('<Q', original, 16 + 84, len(original))
        original.extend(struct.pack('<f', 0.5))
        doc = MdfDocument(original, SOURCE)
        self.assertEqual(doc.MATERIAL_SIZE, 108)
        self.assertEqual(doc.material(0)['properties'][0]['flags'], 0x1234)

    def test_ambiguous_and_corrupt_headers_are_rejected(self):
        data = bytearray(128)
        data[:8] = b'MDF\0\x01\0\x01\0'
        struct.pack_into('<Q', data, 16, 124)
        data[124:128] = b'A\0\0\0'
        with self.assertRaisesRegex(MdfError, '2.51.*歧义'):
            MdfDocument(data, SOURCE)
        struct.pack_into('<Q', data, 16, 4)
        with self.assertRaises(MdfError):
            MdfDocument(data, SOURCE)

    def test_empty_document_keeps_existing_layout(self):
        doc = MdfDocument(b'MDF\0\x01\0\0\0' + bytes(8), SOURCE)
        self.assertEqual(doc.MATERIAL_SIZE, 104)
        self.assertEqual(doc.build()[0], doc.original)
