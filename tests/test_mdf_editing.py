"""Binary editing regressions, independent of the installed game corpus."""
import copy
from pathlib import Path
import struct
import tempfile
import unittest
from types import SimpleNamespace

from mmtr_editor.mdf import MdfDocument, MdfError
from mmtr_editor.rpc import Service


def fixture():
    data = bytearray(400)
    data[:8] = b'MDF\0\x01\0\x02\0'

    def string(value):
        offset = len(data)
        data.extend(value.encode('utf-16-le') + b'\0\0')
        return offset

    shared = string('Textures/shared.tex')
    slot = string('BaseMap')
    prop = string('Value')
    for i in range(2):
        base = 16 + i * 100
        struct.pack_into('<Q', data, base, string(f'Material{i}'))
        struct.pack_into('<I', data, base + 8, 0x12345678)
        struct.pack_into('<III', data, base + 12, 16, 1, 1)
        struct.pack_into('<iiIIi', data, base + 32, 1, 16, 0x8000009b, 0x80000001, 2)
        struct.pack_into('<QQ', data, base + 52, 280 + i * 24, 216 + i * 32)
        struct.pack_into('<Q', data, base + 84, string('MaterialShader/test.mmtr'))
        struct.pack_into('<QIIQ', data, 216 + i * 32, slot, 0x111, 0x222, shared)
        struct.pack_into('<QIIIHH', data, 280 + i * 24, prop, 0x333, 0x444, 0, 1, 0)
    # Omit only the final material's 12 alignment bytes, as the real MDF45 does.
    data.extend(b'\x7c\x7c')
    for i in range(2):
        base = 16 + i * 100
        struct.pack_into('<Q', data, base + 76, len(data))
        data.extend(struct.pack('<f', .5))
        if i == 0:
            data.extend(b'\xab' * 12)
    return bytes(data)


class MdfEditingTests(unittest.TestCase):
    def setUp(self):
        self.original = fixture()
        self.doc = MdfDocument(self.original)

    def test_long_unicode_path_is_isolated_and_deterministic(self):
        other = copy.deepcopy(self.doc.material(1))
        path = 'Art/新纹理/😀/' + 'long_' * 60 + '.tex'
        self.doc.set_texture(0, 0, path)
        data, report = self.doc.build()
        self.assertEqual(MdfDocument(data).material(0)['textures'][0]['path'], path)
        self.assertEqual(MdfDocument(data).material(1), other)
        self.assertEqual(self.doc.build()[0], data)
        self.assertFalse(report['byte_length_unchanged'])
        self.assertTrue(report['unknown_bytes_preserved'])
        # Only one pointer may differ in the original file, including shared strings.
        pointer = 216 + 16
        self.assertEqual(data[:pointer], self.original[:pointer])
        self.assertEqual(data[pointer + 8:len(self.original)], self.original[pointer + 8:])
        self.assertEqual(data[len(self.original):len(self.original) + 12], b'\0' * 12)
        self.assertEqual(sum(x['size'] for x in report['byte_ranges']), 8 + len(data) - len(self.original))

    def test_texture_replacement_does_not_accumulate_stale_strings(self):
        self.doc.set_texture(0, 0, 'Textures/longer_path.tex')
        first = self.doc.build()[0]
        self.doc.set_texture(0, 0, 'x')
        second = self.doc.build()[0]
        self.assertLess(len(second), len(first))
        self.assertEqual(MdfDocument(second).material(0)['textures'][0]['path'], 'x')
        self.doc.set_texture(0, 0, 'Textures/shared.tex')
        self.assertEqual(self.doc.build()[0], self.original)
        self.assertFalse(self.doc.snapshot()['dirty'])
        self.assertEqual(self.doc.edit_notes, [])

    def test_shared_texture_headers_are_rejected_without_mutation(self):
        data = bytearray(self.original)
        struct.pack_into('<Q', data, 116 + 60, 216)
        doc = MdfDocument(data)
        with self.assertRaises(MdfError):
            doc.set_texture(0, 0, 'New/Texture.tex')
        self.assertEqual(doc.build()[0], bytes(data))

    def test_multiple_texture_edits_can_be_reopened_and_reset(self):
        for i in range(2):
            self.doc.set_texture(i, 0, f'Art/{i}.tex')
        self.doc.set_property(1, 'Value', [.25])
        self.doc.set_flags(1, 0xffffffff, 0x81234567)
        self.doc.set_setting(1, 'shader_type', 6)
        exported = self.doc.build()[0]
        reopened = MdfDocument(exported)
        self.assertEqual(reopened.material(1)['flags'], [0xffffffff, 0x81234567])
        self.assertEqual(reopened.material(1)['shader_type'], 6)
        self.assertEqual(reopened.material(1)['properties'][0]['values'], [.25])
        reopened.set_texture(0, 0, 'Art/second-export.tex')
        self.assertEqual(MdfDocument(reopened.build()[0]).material(0)['textures'][0]['path'], 'Art/second-export.tex')
        self.doc.reset()
        self.assertEqual(self.doc.build()[0], self.original)
        self.assertFalse(self.doc.snapshot()['dirty'])

    def test_settings_and_flags_touch_only_selected_fields_and_restore_cleanly(self):
        for field, offset in [('shader_type', 36), ('shader_lod', 48), ('bake_texture_array_size', 32)]:
            original_value = self.doc.material(0)[field]
            self.doc.set_setting(0, field, -0x80000000)
            data, report = self.doc.build()
            self.assertEqual(report['byte_ranges'], [{'offset': 16 + offset, 'size': 4}])
            self.assertEqual(data[:16 + offset], self.original[:16 + offset])
            self.assertEqual(data[20 + offset:], self.original[20 + offset:])
            self.doc.set_setting(0, field, original_value)
        self.doc.set_flags(0, 0xffffffff, 0)
        self.assertEqual(MdfDocument(self.doc.build()[0]).material(0)['flags'], [0xffffffff, 0])
        self.doc.set_flags(0, 0x8000009b, 0x80000001)
        self.assertEqual(self.doc.build()[0], self.original)
        self.assertEqual(self.doc.edit_notes, [])

    def test_invalid_input_cannot_mutate_document(self):
        operations = [
            lambda: self.doc.set_texture(0, 0, ''),
            lambda: self.doc.set_texture(0, 0, 'a\0b'),
            lambda: self.doc.set_texture(0, 0, '\ud800'),
            lambda: self.doc.set_texture(0, 1, 'x'),
            lambda: self.doc.set_setting(0, 'property_count', 1),
            lambda: self.doc.set_setting(0, 'shader_type', True),
            lambda: self.doc.set_setting(0, 'shader_lod', 0x80000000),
            lambda: self.doc.set_flags(0, -1),
            lambda: self.doc.set_flags(0, None, 0x100000000),
            lambda: self.doc.set_property(0, 'Value', [float('nan')]),
        ]
        for operation in operations:
            with self.assertRaises(MdfError):
                operation()
            self.assertEqual(self.doc.build()[0], self.original)

    def test_component_updates_merge_latest_values_and_reject_atomically(self):
        # Change a four-component property through independent updates, like
        # rapid Tab edits and RGB picking. Other channels, including A, survive.
        data = bytearray(self.original)
        struct.pack_into('<H', data, 280 + 20, 4)
        doc = MdfDocument(data)
        before = doc.material(0)['properties'][0]['values'][:]
        doc.set_property_components(0, 'Value', {'0': .25})
        doc.set_property_components(0, 'Value', {'1': .75})
        self.assertEqual(doc.material(0)['properties'][0]['values'], [.25, .75, *before[2:]])
        doc.set_property_components(0, 'Value', {'0': 2, '1': 3, '2': 4})
        self.assertEqual(MdfDocument(doc.build()[0]).material(0)['properties'][0]['values'], [2, 3, 4, before[3]])
        changed = doc.build()[0]
        for invalid in ({'0': 7, '4': 1}, {'-1': 1}, {'00': 1}, {'0': float('nan')}, {'0': '1'}, {}, []):
            with self.assertRaises(MdfError):
                doc.set_property_components(0, 'Value', invalid)
            self.assertEqual(doc.build()[0], changed)

    def test_component_rpc_invalidates_migration_and_keeps_other_values(self):
        service = Service()
        service.mdf = self.doc
        service.mdf_selected = 0
        service.mdf_migration = {'stale': True}
        result = service.handle({'op': 'mdf_set_property_components', 'material_index': 0, 'name': 'Value', 'components': {'0': .25}})
        self.assertEqual(result['selected']['properties'][0]['values'], [.25])
        self.assertIsNone(result['migration'])

    def test_mmtrs_and_gpbf_display_data_survive_unrelated_edits(self):
        data = bytearray(self.original)
        data.extend(b'\0' * 12)
        table = len(data)
        data.extend(b'\0' * 64)
        for i in range(8):
            struct.pack_into('<Q', data, table + i * 8, len(data))
            data.extend(struct.pack('<III', 2, i, 100 + i))
        struct.pack_into('<Q', data, 16 + 92, table)
        gpbf = len(data)
        data.extend(b'\0' * 32)
        for slot, value in ((0, 'BufferName'), (16, 'Buffer/Path')):
            struct.pack_into('<Q', data, gpbf + slot, len(data))
            data.extend(value.encode('utf-16-le') + b'\0\0')
        struct.pack_into('<II', data, 16 + 24, 1, 1)
        struct.pack_into('<Q', data, 16 + 68, gpbf)
        doc = MdfDocument(data)
        self.assertEqual(doc.material(0)['mmtrs'], [[i, 100 + i] for i in range(8)])
        self.assertEqual(doc.material(0)['gpbf'], [{'name': 'BufferName', 'path': 'Buffer/Path'}])
        self.assertEqual(doc.build()[0], bytes(data))
        doc.set_texture(0, 0, 'New/Texture.tex')
        reopened = MdfDocument(doc.build()[0])
        self.assertEqual(reopened.material(0)['mmtrs'], doc.material(0)['mmtrs'])
        self.assertEqual(reopened.material(0)['gpbf'], doc.material(0)['gpbf'])
        struct.pack_into('<I', data, table + 64, 0xffffffff)
        with self.assertRaises(MdfError):
            MdfDocument(data)

    def test_rpc_export_protects_source_and_tracks_saved_state(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'original.mdf2.45'
            out = Path(temp) / 'edited.mdf2.45'
            source.write_bytes(self.original)
            service = Service()
            service.handle({'op': 'mdf_open', 'path': str(source)})
            service.mdf_migration = {'stale': True}
            service.handle({'op': 'mdf_set_setting', 'material_index': 0, 'field': 'shader_type', 'value': 6})
            self.assertIsNone(service.mdf_migration)
            service.handle({'op': 'mdf_set_texture', 'material_index': 0, 'slot': 0, 'path': 'Textures/edited.tex'})
            with self.assertRaises(Exception):
                service.handle({'op': 'mdf_export', 'path': str(source)})
            report = service.handle({'op': 'mdf_export', 'path': str(out)})
            self.assertTrue(report['reparse_valid'])
            self.assertFalse(service.handle({'op': 'mdf_snapshot'})['unsaved'])
            self.assertEqual(MdfDocument.open(out).material(0)['textures'][0]['path'], 'Textures/edited.tex')
            service.handle({'op': 'mdf_reset'})
            self.assertTrue(service.handle({'op': 'mdf_snapshot'})['unsaved'])
            self.assertEqual(source.read_bytes(), self.original)

    def test_structural_property_and_texture_edits_repack_and_round_trip(self):
        self.doc.delete_property(0, 'Value')
        self.doc.add_property(0, 'AddedParam', [1.0, 2.0, 3.0, 4.0], flags=7)
        self.doc.delete_texture(0, 0)
        self.doc.add_texture(0, 'NormalMap', 'Textures/normal.tex')
        data, report = self.doc.build()
        reopened = MdfDocument(data)
        material = reopened.material(0)
        self.assertEqual([(p['name'], p['values'], p['flags']) for p in material['properties']], [('AddedParam', [1.0, 2.0, 3.0, 4.0], 7)])
        self.assertEqual([(t['name'], t['path']) for t in material['textures']], [('NormalMap', 'Textures/normal.tex')])
        self.assertEqual(reopened.material(1)['properties'][0]['values'], [.5])
        self.assertTrue(report['reparse_valid'])
        self.assertEqual(self.doc.build()[0], data)

    def test_structural_reset_restores_original(self):
        self.doc.add_property(0, 'AddedParam', [1.0])
        self.doc.add_texture(0, 'NormalMap', 'Textures/normal.tex')
        self.doc.reset()
        self.assertEqual(self.doc.build()[0], self.original)
        self.assertFalse(self.doc.snapshot()['dirty'])

    def test_master_shared_string_changes_only_selected_pointer_and_appended_text(self):
        original = bytearray(self.original)
        pointer = struct.unpack_from('<Q', original, 16 + 84)[0]
        struct.pack_into('<Q', original, 116 + 84, pointer)
        doc = MdfDocument(original)
        other = copy.deepcopy(doc.material(1))
        path = 'MaterialShader/Variation/新目录/测试😀.mmtr'
        doc.set_master(0, path)
        data, report = doc.build()
        self.assertEqual(MdfDocument(data).material(0)['master'], path)
        self.assertEqual(MdfDocument(data).material(1), other)
        self.assertEqual(data[:100], bytes(original[:100]))
        self.assertEqual(data[108:len(original)], bytes(original[108:]))
        self.assertEqual(report['byte_ranges'][0], {'offset': 100, 'size': 8})
        self.assertTrue(doc.snapshot()['dirty'])
        self.assertEqual(doc.build()[0], data)
        doc.set_master(0, 'MaterialShader/test.mmtr')
        self.assertEqual(doc.build()[0], bytes(original))
        self.assertFalse(doc.snapshot()['dirty'])

    def test_master_combines_with_structural_and_texture_edits_and_resets(self):
        self.doc.set_texture(1, 0, 'systems/rendering/NullGray.tex')
        self.doc.set_master(0, 'MaterialShader/Variation/Longer_Master.mmtr')
        self.doc.add_property(0, 'Added', [0]*4)
        self.doc.add_texture(0, 'NewMap', 'systems/rendering/NullBlack.tex')
        data = self.doc.build()[0]
        reopened = MdfDocument(data)
        self.assertEqual(reopened.material(0)['master'], 'MaterialShader/Variation/Longer_Master.mmtr')
        self.assertEqual(reopened.material(0)['properties'][-1]['name'], 'Added')
        self.assertEqual(reopened.material(0)['textures'][-1]['name'], 'NewMap')
        self.assertEqual(reopened.material(1)['textures'][0]['path'], 'systems/rendering/NullGray.tex')
        self.doc.set_master(0, 'MaterialShader/test.mmtr')
        self.assertNotIn('MaterialShader/Variation/Longer_Master.mmtr'.encode('utf-16-le'), self.doc.build()[0])
        self.assertEqual(MdfDocument(self.doc.build()[0]).material(0)['master'], 'MaterialShader/test.mmtr')
        self.doc.reset()
        self.assertEqual(self.doc.build()[0], self.original)

    def test_texture_path_on_structural_material_survives_rebuild_in_either_order(self):
        # 重建结构表时字符串缓存须以原字节为键；以当前路径为键会把新路径指回旧字符串，重解析校验随之拒绝。
        for first_texture in (True, False):
            with self.subTest(first_texture=first_texture):
                doc = MdfDocument(self.original)
                edits = [lambda: doc.set_texture(0, 0, 'Textures/new_path.tex'), lambda: doc.add_property(0, 'Added', [0]*4)]
                for edit in (edits if first_texture else edits[::-1]):
                    edit()
                reopened = MdfDocument(doc.build()[0])
                self.assertEqual(reopened.material(0)['textures'][0]['path'], 'Textures/new_path.tex')
                self.assertEqual(reopened.material(0)['properties'][-1]['name'], 'Added')
                self.assertEqual(reopened.material(1)['textures'][0]['path'], 'Textures/shared.tex')

    def test_invalid_master_is_rejected_before_mutation_and_rpc_clears_links(self):
        for value in ('', '../test.mmtr', '/MaterialShader/test.mmtr', 'C:/test.mmtr',
                      'MaterialShader/../test.mmtr', 'MaterialShader/test.mmtr.240906211',
                      'MaterialShader/a\0.mmtr', 'MaterialShader/a\n.mmtr', 'MaterialShader/\ud800.mmtr'):
            with self.subTest(value=repr(value)):
                with self.assertRaises(MdfError):
                    self.doc.set_master(0, value)
                self.assertEqual(self.doc.build()[0], self.original)
        service = Service()
        service.mdf, service.mdf_selected = self.doc, 0
        service.mdf_link_doc = object()
        service.mdf_migration = {'stale': True}
        result = service.handle({'op': 'mdf_set_master', 'material_index': 0,
                                 'path': 'MaterialShader\\Variation\\test.mmtr'})
        self.assertEqual(result['selected']['master'], 'MaterialShader/Variation/test.mmtr')
        self.assertIsNone(service.mdf_link_doc)
        self.assertIsNone(result['migration'])

    def test_rpc_rejects_new_mmtr_interfaces_but_allows_existing_names(self):
        service = Service()
        service.mdf = self.doc
        service.mdf_selected = 0
        service.doc = SimpleNamespace(info={'metadata': {
            'cbuffers': [{'name': 'UserMaterial', 'members': [{'name': 'AddedParam'}]}],
            'srvs': [{'name': 'NormalMap'}],
        }})
        with self.assertRaises(MdfError):
            service.handle({'op': 'mdf_add_property', 'material_index': 0, 'name': 'Unsupported', 'values': [0]})
        service._require_mdf_interface_name('property', 'AddedParam')
        self.doc.add_property(0, 'AddedParam', [0])
        with self.assertRaises(MdfError):
            service.handle({'op': 'mdf_add_texture', 'material_index': 0, 'name': 'Unsupported', 'path': 'x.tex'})
        service._require_mdf_interface_name('texture', 'NormalMap')


if __name__ == '__main__':
    unittest.main()
