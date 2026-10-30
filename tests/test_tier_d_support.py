"""Tier D per-target SDF header（0x110）的合成测试；不依赖游戏文件或 DXC。"""
from pathlib import Path
import struct
import unittest

from inspect_mmtr import inspect_data
from mmtr_editor.model import Document, EditError
from mmtr_editor.structure import parse_structure
from test_structure import fixture


PATHS = {
    251112994: ('RE9', Path('synthetic.mmtr.251112994')),
    251112995: ('PRAGMATA', Path('synthetic.mmtr.251112995')),
    251112814: ('ONIMUSHAWOTSDEMO', Path('synthetic.mmtr.251112814')),
    260511270: ('DD2', Path('synthetic.mmtr.260511270')),
    260605155: ('ONIMUSHAWOTS', Path('synthetic.mmtr.260605155')),
}


def per_target_fixture(counts=(2, 1, 1, 1)):
    return fixture(4, header_layout='per-target', program_counts=list(counts))


class TierDHeaderTests(unittest.TestCase):
    def test_registered_versions_parse_per_target_counts(self):
        data = per_target_fixture()
        for version, (game, path) in PATHS.items():
            with self.subTest(version=version):
                _, info = inspect_data(data, path)
                self.assertEqual((info['game'], info['record_size']), (game, 0x110))
                self.assertEqual((info['header_layout'], info['record_start']), ('per-target', 0x18))
                self.assertEqual(info['program_counts'], [2, 1, 1, 1])
                self.assertEqual([g['record_count'] for g in info['candidate_groups']], [2, 1, 1, 1])
                self.assertEqual([r['group_index'] for r in info['records']], [0, 0, 1, 2, 3])

    def test_document_rename_and_rebuild_preserve_header_and_counts(self):
        path = PATHS[251112994][1]
        data = per_target_fixture()
        doc = Document(data, path)
        self.assertEqual((doc.info['header_layout'], doc.info['record_count']), ('per-target', 5))
        doc.rename(doc.info['records'][0]['name_offset'], 'Test')
        rebuilt, report = doc.build()
        self.assertTrue(report['reparse_valid'])
        _, info = inspect_data(rebuilt, path)
        self.assertEqual((info['header_layout'], info['program_counts']), ('per-target', [2, 1, 1, 1]))
        self.assertEqual(rebuilt[14:16], b'\0\0')
        self.assertEqual(rebuilt[:0x18], data[:0x18])
        self.assertEqual(rebuilt[0x18:0x18 + 5 * 0x110], data[0x18:0x18 + 5 * 0x110])

    def test_metadata_edit_relocates_per_target_shader_pointer(self):
        path = PATHS[251112994][1]
        doc = Document(per_target_fixture(), path)
        doc.add_cbuffer_member('UserMaterial', 'Added', 4, 16)
        rebuilt, report = doc.build()
        self.assertTrue(report['reparse_valid'])
        _, info = inspect_data(rebuilt, path)
        self.assertEqual((info['header_layout'], info['program_counts']), ('per-target', [2, 1, 1, 1]))
        self.assertEqual([m['name'] for m in info['metadata']['cbuffers'][0]['members']], ['Value', 'Added'])
        self.assertEqual(struct.unpack_from('<Q', rebuilt, 0x10)[0], info['shader_start'])

    def test_structure_uses_declared_target_boundaries(self):
        data = per_target_fixture()
        structure = parse_structure(data, PATHS[251112995][1])
        self.assertEqual([p.target for p in structure.programs], [0, 0, 1, 2, 3])
        self.assertEqual([p.name for p in structure.programs[:2]], ['Pass0', 'Pass1'])
        self.assertEqual(structure.diagnostics, ())

    def test_nonzero_header_padding_is_rejected(self):
        data = bytearray(per_target_fixture())
        data[14] = 1
        with self.assertRaisesRegex(ValueError, 'padding is non-zero'):
            inspect_data(bytes(data), PATHS[251112994][1])

    def test_mixed_zero_counts_are_rejected(self):
        data = per_target_fixture((2, 0, 1, 1))
        with self.assertRaisesRegex(ValueError, 'at least one program'):
            inspect_data(data, PATHS[251112994][1])

    def test_unregistered_target_count_is_rejected_by_document(self):
        data = fixture(3, header_layout='per-target', program_counts=[1, 1, 1])
        with self.assertRaises(EditError):
            Document(data, PATHS[251112994][1])
