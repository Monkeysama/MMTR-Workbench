"""四/五组贯穿全部记录、共享引用、候选重建和另存重开；无需游戏资产。"""
from pathlib import Path
import struct
import tempfile
import unittest

from test_structure import fixture, PATH
from mmtr_editor.hashes import xxh64
from mmtr_editor.model import Document, EditError
from mmtr_editor.structure import document_structure


def resized(blob):
    """只扩展合成 DXIL 容器，不能用作可执行 Shader。"""
    result = bytearray(blob)
    struct.pack_into('<I', result, 24, len(result)+4)
    struct.pack_into('<I', result, 40, struct.unpack_from('<I', result, 40)[0]+4)
    struct.pack_into('<I', result, 48, struct.unpack_from('<I', result, 48)[0]+1)
    struct.pack_into('<I', result, 64, struct.unpack_from('<I', result, 64)[0]+4)
    return bytes(result)+bytes(4)


class GroupLayoutTests(unittest.TestCase):
    def test_all_groups_relocate_shared_shaders_and_export_reopens(self):
        for groups in (4, 5):
            with self.subTest(groups=groups), tempfile.TemporaryDirectory() as folder:
                raw = fixture(groups, 918)
                first = Document(raw, PATH)
                second = resized(first.shader_bytes(0))
                raw += second+struct.pack('<Q', xxh64(second, 0xffffffff))
                doc = Document(raw, PATH)
                count = groups*918
                before = document_structure(doc)
                self.assertEqual((before.header.group_count, len(before.programs)), (groups, count))
                self.assertEqual(len(before.shader_users[0]), count)
                self.assertEqual(before.programs[-1].target, groups-1)
                self.assertEqual(doc.build()[0], raw)
                doc.redirect(count-1, 0x30, 1)
                doc.replace_shader(0, resized(second))
                rebuilt, report = doc.build()
                self.assertTrue(report['reparse_valid'])
                candidate = document_structure(doc)
                self.assertEqual(candidate.diagnostics, ())
                self.assertEqual(len(candidate.shader_users[0]), count-1)
                self.assertEqual(candidate.shader_users[1][0].program, count-1)
                for index, program in enumerate(candidate.programs):
                    self.assertEqual(program.raw[0x100], 99)
                    self.assertEqual(program.shaders[0].shader_index, int(index == count-1))
                    self.assertEqual(program.tables, before.programs[index].tables)
                target = Path(folder)/PATH.name
                doc.export(target)
                reopened = Document.open(target)
                self.assertEqual(reopened.build()[0], rebuilt)
                self.assertEqual(reopened.check_original_integrity(), doc.check_original_integrity())
                self.assertEqual(reopened.integrity['name_hash_mismatches'], [])
                self.assertEqual(reopened.integrity['shader_trailer_mismatches'], [])

    def test_five_group_shared_metadata_and_names_are_not_truncated(self):
        doc = Document(fixture(5, 3), PATH)
        doc.add_cbuffer_member('UserMaterial', 'Added', 4, 16)
        candidate = document_structure(doc)
        self.assertEqual(len(candidate.programs), 15)
        self.assertEqual(candidate.diagnostics, ())
        buffers = [o for o in candidate.objects if o.kind == 'cbuffers_metadata']
        self.assertTrue(buffers)
        self.assertTrue(all(len(o.users) == 15 for o in buffers))
        reopened = Document(doc.build()[0], PATH)
        cb = reopened.info['metadata']['cbuffers'][0]
        self.assertEqual([m['name'] for m in cb['members']], ['Value', 'Added'])
        pointer = reopened.info['records'][-1]['name_offset']
        reopened.rename(pointer, 'Last')
        renamed = Document(reopened.build()[0], PATH)
        self.assertEqual([r['name'] for r in renamed.info['records'] if r['index'] % 3 == 2], ['Last']*5)

    def test_unknown_or_inconsistent_groups_are_rejected(self):
        for groups in (3, 6):
            with self.subTest(groups=groups), self.assertRaisesRegex(EditError, '四组或五组'):
                Document(fixture(groups), PATH)
        raw = bytearray(fixture(5, 2))
        struct.pack_into('<Q', raw, 16+9*0x110, struct.unpack_from('<Q', raw, 16)[0])
        with self.assertRaisesRegex(EditError, '同名序列'):
            Document(raw, PATH)
        raw = bytearray(fixture(4))
        struct.pack_into('<H', raw, 4, 5)
        with self.assertRaises(EditError):
            Document(raw, PATH)


if __name__ == '__main__':
    unittest.main()
