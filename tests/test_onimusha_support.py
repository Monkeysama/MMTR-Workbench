"""鬼武者 MMTR 头与 MDF 2.51 的合成测试；不依赖游戏文件或 DXC。"""
from pathlib import Path
import struct
import tempfile
import unittest

from inspect_mmtr import inspect_data
from mmtr_editor.mdf import MDF_LAYOUTS, MdfDocument
from mmtr_editor.model import Document
from test_structure import fixture


MMTR = Path('synthetic.mmtr.260605155')


def mdf51_fixture(material_count=2):
    layout = MDF_LAYOUTS[51]
    size = layout['material_size']
    texture_size = layout['texture_size']
    property_size = layout['property_size']
    texture_base = (16 + material_count * size + 7) // 8 * 8
    property_base = (texture_base + material_count * texture_size + 7) // 8 * 8
    data_base = (property_base + material_count * property_size + 7) // 8 * 8
    block_size = 16
    data = bytearray(data_base + block_size)
    data[:8] = b'MDF\0\x01\0' + struct.pack('<H', material_count)

    def string(value):
        offset = len(data)
        data.extend(value.encode('utf-16-le') + b'\0\0')
        return offset

    shared = string('Textures/shared.tex')
    slot = string('BaseMap')
    prop = string('Value')
    for i in range(material_count):
        base = 16 + i * size
        struct.pack_into('<Q', data, base + layout['name'], string(f'Material{i}'))
        struct.pack_into('<I', data, base + layout['name_hash'], 0x12345678)
        struct.pack_into('<I', data, base + layout['params_size'], block_size)
        struct.pack_into('<I', data, base + layout['prop_count'], 1)
        struct.pack_into('<I', data, base + layout['float_count'], block_size // 4)
        struct.pack_into('<I', data, base + layout['tex_count'], 1)
        for field in ('gpbf_name_count', 'gpbf_path_count'):
            struct.pack_into('<I', data, base + layout[field], 0)
        struct.pack_into('<i', data, base + layout['bake'], 0)
        struct.pack_into('<i', data, base + layout['shader_type'], 1)
        struct.pack_into('<i', data, base + layout['shader_lod'], 0)
        for lane in range(layout['flags_count']):
            struct.pack_into('<I', data, base + layout['flags'] + lane * 4, 0)
        struct.pack_into('<Q', data, base + layout['prop_headers'], property_base + i * property_size)
        struct.pack_into('<Q', data, base + layout['tex_headers'], texture_base + i * texture_size)
        struct.pack_into('<Q', data, base + layout['prop_data'], data_base)
        struct.pack_into('<Q', data, base + layout['master'], string('MaterialShader/mastermaterial/test.mmtr'))
        struct.pack_into('<Q', data, base + layout['gpbf'], 0)
        struct.pack_into('<Q', data, base + layout['mmtrs'], 0)
        struct.pack_into('<QIIQ', data, texture_base + i * texture_size, slot, 0x111, 0x222, shared)
        struct.pack_into('<QIIIHH', data, property_base + i * property_size,
                         prop, 0x333, 0x444, i * 4, 1, 0)
        struct.pack_into('<f', data, data_base + i * 4, 0.5 + i)
    return bytes(data)


class OnimushaMMTRTests(unittest.TestCase):
    def test_header_and_material_offset_are_reported(self):
        data = fixture(4, header_layout='per-target', program_counts=[1, 1, 1, 1])
        _, info = inspect_data(data, MMTR)
        self.assertEqual((info['game'], info['header_size']), ('ONIMUSHAWOTS', 0x18))
        self.assertEqual(info['shader_start_field'], 0x10)
        self.assertEqual(info['material_header_bytes'], 16)

    def test_rename_preserves_24_byte_header(self):
        data = fixture(4, header_layout='per-target', program_counts=[1, 1, 1, 1])
        doc = Document(data, MMTR)
        doc.rename(doc.info['records'][0]['name_offset'], 'Test')
        rebuilt, report = doc.build()
        self.assertTrue(report['reparse_valid'])
        self.assertEqual(rebuilt[:0x18], data[:0x18])



class Mdf51Tests(unittest.TestCase):
    def open(self):
        handle = tempfile.NamedTemporaryFile(suffix='.mdf2.51', delete=False)
        handle.write(mdf51_fixture())
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return MdfDocument.open(handle.name)

    def test_layout_and_float_count_round_trip(self):
        doc = self.open()
        self.assertEqual((doc.game, doc.MATERIAL_SIZE), ('OnimushaWotS', 104))
        material = doc.material(0)
        self.assertEqual(material['properties'][0]['values'], [0.5])
        self.assertEqual(material['property_float_count'], 4)
        doc.set_property(0, 'Value', [1.5])
        result, _ = doc.build()
        again = MdfDocument(result, doc.source)
        self.assertEqual(again.material(0)['properties'][0]['values'], [1.5])
        self.assertEqual(again.material(0)['property_float_count'], 4)

    def test_structural_texture_and_master_round_trip(self):
        doc = self.open()
        doc.add_texture(0, 'ExtraMap', 'MaterialShader/Textures/extra.tex')
        doc.set_master(0, 'MaterialShader/mastermaterial/edited.mmtr')
        result, _ = doc.build()
        again = MdfDocument(result, doc.source)
        self.assertEqual(again.material(0)['textures'][-1]['name'], 'ExtraMap')
        self.assertEqual(again.material(0)['textures'][-1]['path'], 'MaterialShader/Textures/extra.tex')
        self.assertEqual(again.material(0)['master'], 'MaterialShader/mastermaterial/edited.mmtr')
    def test_structural_add_updates_float_count(self):
        doc = self.open()
        doc.add_property(0, 'Added', [1.0, 2.0], offset=4)
        result, _ = doc.build()
        again = MdfDocument(result, doc.source)
        added = next(p for p in again.material(0)['properties'] if p['name'] == 'Added')
        self.assertEqual(added['values'], [1.0, 2.0])
        self.assertEqual(again.material(0)['property_float_count'], 3)


if __name__ == '__main__':
    unittest.main()


