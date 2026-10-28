"""RE4（MMTR 221007879 / MDF 2.32）的记录长度、组数、格式识别与重定位；只用合成数据，不依赖游戏资产。"""
from pathlib import Path
import struct
import tempfile
import unittest

from inspect_mmtr import inspect_data
from mmtr_editor.hashes import xxh64
from mmtr_editor.mdf import MdfDocument, MdfError
from mmtr_editor.model import Document, EditError
from mmtr_editor.structure import document_structure, parse_structure
from test_group_layouts import resized
from test_mdf_editing import fixture as mdf_fixture
from test_structure import fixture, PATH as WILDS_PATH

RE4_PATH = Path('_Chainsaw/MasterMaterial/Master/synthetic.mmtr.221007879')


def re4_fixture(groups=3, programs=1):
    return fixture(groups, programs, record_size=0x108)


class Re4MmtrTests(unittest.TestCase):
    def test_layout_is_selected_by_resource_version(self):
        _, info = inspect_data(re4_fixture(), RE4_PATH)
        self.assertEqual((info['game'], info['record_size'], info['resource_version']), ('RE4', 0x108, 221007879))
        self.assertEqual((info['record_count'], info['record_end']), (3, 16+3*0x108))
        _, info = inspect_data(fixture(), WILDS_PATH)
        self.assertEqual((info['game'], info['record_size']), ('MHWilds', 0x110))

    def test_wrong_version_suffix_is_rejected(self):
        with self.assertRaises(EditError):
            Document(re4_fixture(), WILDS_PATH)
        with self.assertRaises(EditError):
            Document(fixture(4), RE4_PATH)

    def test_missing_or_unknown_suffix_accepts_only_the_layout_that_parses(self):
        for name in ('synthetic.mmtr', 'synthetic.mmtr.1'):
            with self.subTest(name=name):
                self.assertEqual(inspect_data(re4_fixture(), Path(name))[1]['record_size'], 0x108)
                self.assertEqual(inspect_data(fixture(), Path(name))[1]['record_size'], 0x110)
        with self.assertRaises(ValueError):
            inspect_data(b'SDF\0' + bytes(60), Path('synthetic.mmtr'))

    def test_group_counts_are_checked_per_game(self):
        Document(re4_fixture(3), RE4_PATH)
        for groups in (4, 5):
            with self.subTest(groups=groups), self.assertRaisesRegex(EditError, 'RE4 三组'):
                Document(re4_fixture(groups), RE4_PATH)
        with self.assertRaisesRegex(EditError, '四组或五组'):
            Document(fixture(3), WILDS_PATH)

    def test_structure_reads_compute_group_and_topology_at_shifted_offsets(self):
        raw = bytearray(re4_fixture())
        base = 16+0x108
        struct.pack_into('<3HH', raw, base+0x100, 64, 2, 1, 5)
        model = parse_structure(bytes(raw), RE4_PATH)
        self.assertEqual(model.diagnostics, ())
        self.assertEqual(model.programs[1].compute_group, (64, 2, 1))
        self.assertEqual(model.programs[1].topology_raw, 5)
        self.assertEqual(model.programs[0].compute_group, (0, 0, 0))
        self.assertEqual(len(model.programs[1].raw), 0x108)

    def test_shader_relocation_and_export_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = re4_fixture(3, 2)
            second = resized(Document(raw, RE4_PATH).shader_bytes(0))
            raw += second+struct.pack('<Q', xxh64(second, 0xffffffff))
            doc = Document(raw, RE4_PATH)
            self.assertEqual(doc.build()[0], raw)
            before = document_structure(doc)
            doc.redirect(5, 0x30, 1)
            doc.replace_shader(0, resized(second))
            rebuilt, report = doc.build()
            self.assertTrue(report['reparse_valid'])
            candidate = document_structure(doc)
            self.assertEqual(candidate.diagnostics, ())
            self.assertEqual(candidate.shader_users[1][0].program, 5)
            for index, program in enumerate(candidate.programs):
                self.assertEqual(program.tables, before.programs[index].tables)
                self.assertEqual(program.raw[0x40:0xB8], before.programs[index].raw[0x40:0xB8])
            target = Path(folder)/RE4_PATH.name
            doc.export(target)
            reopened = Document.open(target)
            self.assertEqual(reopened.build()[0], rebuilt)
            self.assertEqual(reopened.info['record_size'], 0x108)
            self.assertEqual(reopened.integrity['shader_trailer_mismatches'], [])

    def test_metadata_edit_keeps_record_size(self):
        doc = Document(re4_fixture(3, 2), RE4_PATH)
        doc.add_cbuffer_member('UserMaterial', 'Added', 4, 16)
        rebuilt = doc.build()[0]
        _, info = inspect_data(rebuilt, RE4_PATH)
        self.assertEqual(info['record_size'], 0x108)
        self.assertEqual([m['name'] for m in info['metadata']['cbuffers'][0]['members']], ['Value', 'Added'])
        self.assertEqual(document_structure(doc).diagnostics, ())


class Re4MdfTests(unittest.TestCase):
    def test_mdf232_opens_with_re4_master_root(self):
        doc = MdfDocument(mdf_fixture(), Path('synthetic.mdf2.32'))
        self.assertEqual((doc.version, doc.game, doc.snapshot()['game']), (32, 'RE4', 'RE4'))
        doc.set_master(0, '_Chainsaw/MasterMaterial/Variation/test.mmtr')
        self.assertEqual(MdfDocument(doc.build()[0], Path('synthetic.mdf2.32')).material(0)['master'],
                         '_Chainsaw/MasterMaterial/Variation/test.mmtr')
        with self.assertRaisesRegex(MdfError, '_Chainsaw/MasterMaterial/'):
            doc.set_master(0, 'MaterialShader/test.mmtr')
        wilds = MdfDocument(mdf_fixture(), Path('synthetic.mdf2.45'))
        with self.assertRaisesRegex(MdfError, 'MaterialShader/'):
            wilds.set_master(0, '_Chainsaw/MasterMaterial/test.mmtr')

    def test_unregistered_mdf_version_is_rejected(self):
        with self.assertRaisesRegex(MdfError, '2.32'):
            MdfDocument(mdf_fixture(), Path('synthetic.mdf2.31'))


if __name__ == '__main__':
    unittest.main()
