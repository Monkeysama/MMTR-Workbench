"""Synthetic coverage for MDF 2.10-2.23 layouts used by the older RE titles."""

from pathlib import Path
import struct
import tempfile
import unittest

from mmtr_editor.mdf import MDF_LAYOUTS, MdfDocument, MdfError


def fixture(version: int, material_count: int = 2) -> bytes:
    layout = MDF_LAYOUTS[version]
    size = layout['material_size']
    texture_size = layout['texture_size']
    property_size = layout['property_size']
    texture_base = (16 + material_count * size + 7) // 8 * 8
    property_base = (texture_base + material_count * texture_size + 7) // 8 * 8
    data_base = (property_base + material_count * property_size + 7) // 8 * 8
    block_size = 16
    data = bytearray(data_base + block_size)
    data[:8] = b'MDF\0\x01\0' + struct.pack('<H', material_count)

    def string(value: str) -> int:
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
        struct.pack_into('<III', data, base + layout['params_size'], 16, 1, 1)
        for field in ('gpbf_name_count', 'gpbf_path_count'):
            offset = layout[field]
            if offset is not None:
                struct.pack_into('<I', data, base + offset, 0)
        for field in ('bake', 'shader_type', 'shader_lod'):
            offset = layout[field]
            if offset is not None:
                struct.pack_into('<i', data, base + offset, 1 if field == 'shader_type' else 0)
        for lane in range(layout['flags_count']):
            struct.pack_into('<I', data, base + layout['flags'] + lane * 4, 0)
        struct.pack_into('<Q', data, base + layout['prop_headers'], property_base + i * property_size)
        struct.pack_into('<Q', data, base + layout['tex_headers'], texture_base + i * texture_size)
        struct.pack_into('<Q', data, base + layout['prop_data'], data_base)
        struct.pack_into('<Q', data, base + layout['master'], string('mastermaterial/master/test.mmtr'))
        if layout['gpbf'] is not None:
            struct.pack_into('<Q', data, base + layout['gpbf'], 0)
        if layout['mmtrs'] is not None:
            struct.pack_into('<Q', data, base + layout['mmtrs'], 0)
        if layout['shader_redirects'] is not None:
            struct.pack_into('<Q', data, base + layout['shader_redirects'], 0)
        struct.pack_into('<QIIQ', data, texture_base + i * texture_size, slot, 0x111, 0x222, shared)
        if layout['prop_layout'] == 'v31':
            struct.pack_into('<QIIIHH', data, property_base + i * property_size,
                             prop, 0x333, 0x444, i * 4, 1, 0)
        else:
            struct.pack_into('<QIIII', data, property_base + i * property_size,
                             prop, 0x333, 0x444, 1, i * 4)
        struct.pack_into('<f', data, data_base + i * 4, 0.5 + i)
    return bytes(data)


class OldMdfTests(unittest.TestCase):
    def open(self, version: int):
        handle = tempfile.NamedTemporaryFile(suffix=f'.mdf2.{version}', delete=False)
        handle.write(fixture(version))
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return MdfDocument.open(handle.name)

    def test_old_layouts_open_with_expected_headers(self):
        expected = {10: ('RE2', 64, 24), 13: ('RE3', 64, 32), 19: ('RE8', 80, 32),
                    21: ('RE2RT', 80, 32), 23: ('MHRISE', 80, 32)}
        for version, (game, material_size, texture_size) in expected.items():
            with self.subTest(version=version):
                doc = self.open(version)
                self.assertEqual(doc.game, game)
                self.assertEqual(doc.MATERIAL_SIZE, material_size)
                self.assertEqual(doc.TEXTURE_SIZE, texture_size)
                self.assertEqual(doc.material(0)['properties'][0]['values'], [0.5])
                self.assertEqual(doc.material(0)['shader_type'], 1)
                self.assertEqual(doc.material(0)['flags'], [0, 0])

    def test_each_old_layout_round_trips_a_master_and_property_edit(self):
        for version in (10, 13, 19, 21, 23):
            with self.subTest(version=version):
                doc = self.open(version)
                root = '_ge/mastermaterial/master/' if version == 19 else 'mastermaterial/master/'
                doc.set_master(0, root + 'edited.mmtr')
                doc.set_property(0, 'Value', [2.5])
                result, _ = doc.build()
                again = MdfDocument(result, doc.source)
                self.assertEqual(again.material(0)['master'], root + 'edited.mmtr')
                self.assertEqual(again.material(0)['properties'][0]['values'], [2.5])

    def test_texture_edit_round_trips_for_24_and_32_byte_headers(self):
        for version in (10, 13, 19):
            with self.subTest(version=version):
                doc = self.open(version)
                doc.set_texture(1, 0, 'Textures/edited.tex')
                result, _ = doc.build()
                again = MdfDocument(result, doc.source)
                self.assertEqual(again.material(1)['textures'][0]['path'], 'Textures/edited.tex')

    def test_structural_property_and_texture_edits_use_version_header_format(self):
        for version in (10, 13, 19):
            with self.subTest(version=version):
                doc = self.open(version)
                doc.add_property(0, 'Added', [1.0, 2.0], offset=4)
                doc.add_texture(0, 'ExtraMap', 'Textures/extra.tex')
                result, _ = doc.build()
                again = MdfDocument(result, doc.source)
                added = next(p for p in again.material(0)['properties'] if p['name'] == 'Added')
                self.assertEqual(added['values'], [1.0, 2.0])
                self.assertEqual(again.material(0)['textures'][-1]['name'], 'ExtraMap')

    def test_absent_flags_and_settings_are_rejected(self):
        for version in (10, 13, 19):
            with self.subTest(version=version):
                doc = self.open(version)
                with self.assertRaises(MdfError):
                    doc.set_flags(0, None, 1)
                with self.assertRaises(MdfError):
                    doc.set_setting(0, 'shader_lod', 1)
                with self.assertRaises(MdfError):
                    doc.set_setting(0, 'bake_texture_array_size', 1)
                if version == 10:
                    with self.assertRaises(MdfError):
                        doc.add_property(0, 'Flagged', [1.0], flags=1)

    def test_old_property_flags_survive_structural_changes(self):
        for version in (13, 19, 21, 23):
            for operation in ('add_texture', 'delete_texture', 'add_property', 'delete_property'):
                with self.subTest(version=version, operation=operation):
                    data = bytearray(fixture(version))
                    layout = MDF_LAYOUTS[version]
                    for index, flags in ((0, 0x1234), (1, 0x4321)):
                        table = struct.unpack_from('<Q', data, 16 + index * layout['material_size'] + layout['prop_headers'])[0]
                        struct.pack_into('<H', data, table + 22, flags)
                    source = Path(f'sample.mdf2.{version}')
                    doc = MdfDocument(data, source)
                    self.assertEqual(doc.material(0)['properties'][0]['flags'], 0x1234)
                    if operation == 'add_texture':
                        doc.add_texture(0, 'ExtraMap', 'Textures/extra.tex')
                    elif operation == 'delete_texture':
                        doc.delete_texture(0, 0)
                    elif operation == 'add_property':
                        doc.add_property(0, 'Added', [1.0], flags=0x4567, offset=4)
                    else:
                        doc.add_property(0, 'Added', [1.0], flags=0x4567, offset=4)
                        initial, _ = doc.build()
                        doc = MdfDocument(initial, source)
                        doc.delete_property(0, 'Added')
                    result, report = doc.build()
                    again = MdfDocument(result, source)
                    self.assertEqual(again.material(0)['properties'][0]['flags'], 0x1234)
                    self.assertEqual(again.material(1)['properties'][0]['flags'], 0x4321)
                    self.assertTrue(report['reparse_valid'])
                    if operation == 'add_property':
                        self.assertEqual(again.material(0)['properties'][1]['flags'], 0x4567)

    def test_rejected_combined_flags_leave_rpc_candidate_unchanged(self):
        from mmtr_editor.rpc import Service

        for version in (10, 13, 19, 21, 23):
            for prior_edit in (False, True):
                with self.subTest(version=version, prior_edit=prior_edit):
                    doc = self.open(version)
                    if prior_edit:
                        doc.set_flags(0, 7)
                    service = Service()
                    service.mdf = doc
                    before_snapshot = doc.snapshot(0)
                    before_bytes, before_report = doc.build()
                    with self.assertRaisesRegex(MdfError, '没有第二个 flags'):
                        service.handle({'op': 'mdf_set_flags', 'material_index': 0,
                                        'flags_a': 1, 'flags_b': 1})
                    self.assertEqual(doc.snapshot(0), before_snapshot)
                    after_bytes, after_report = doc.build()
                    self.assertEqual(after_bytes, before_bytes)
                    self.assertEqual(after_report['changes'], before_report['changes'])

    def test_re8_master_root_is_enforced(self):
        doc = self.open(19)
        with self.assertRaises(MdfError):
            doc.set_master(0, 'mastermaterial/master/wrong-root.mmtr')


if __name__ == '__main__':
    unittest.main()
