"""RE4（MMTR 221007879 / MDF 2.32）的记录长度、组数、格式识别与重定位；只用合成数据，不依赖游戏资产。"""
from pathlib import Path
import struct
import tempfile
import unittest

from inspect_mmtr import inspect_data
from mmtr_editor.hashes import xxh64
from mmtr_editor.material_addresses import material_reads
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


# RE4 可见性缓冲变体的网格表读取（形态取自原版 789 个 Shader）；%redir 一行为普通材质读取作对照。
def mesh_ir(element=52, buffer='Instances', mixed=False):
    pick = '%x, i32 %other' if mixed else '%x, i32 %z'
    return f'''; BindlessBuffer                    texture  struct         r/o      T0             t0     1
; {buffer}                         texture  struct         r/o      T4             t1     1
define void @main() {{
  %bb = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 0, i32 0, i32 0, i1 false)
  %inst = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 0, i32 4, i32 1, i1 false)
  %ir = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %inst, i32 %i, i32 {element}, i8 1, i32 4)
  %ib = extractvalue %dx.types.ResRet.i32 %ir, 0
  %base = sdiv i32 %ib, 16
  %s16 = lshr i32 %packed, 16
  %m = and i32 %s16, 255
  %p1 = add nuw nsw i32 %m, 1
  %row = lshr i32 %p1, 1
  %addr = add nsw i32 %row, %base
  %mesh = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %bb, i32 %addr, i32 0, i8 15, i32 4)
  %x = extractvalue %dx.types.ResRet.i32 %mesh, 0
  %z = extractvalue %dx.types.ResRet.i32 %mesh, 2
  %ptr = select i1 %odd, i32 {pick}
  %sub = lshr i32 %ptr, 4
  %subrow = add nuw nsw i32 %sub, 2
  %rec = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %bb, i32 %subrow, i32 0, i8 15, i32 4)
  %rx = extractvalue %dx.types.ResRet.i32 %rec, 0
  %mat = lshr i32 %redir, 4
  %matrow = add i32 %mat, 3
  %load = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %bb, i32 %matrow, i32 0, i8 15, i32 4)
  %field = extractvalue %dx.types.ResRet.i32 %load, 1
  ret void
}}
'''


class Re4MeshTableTests(unittest.TestCase):
    def test_mesh_table_and_chained_reads_are_not_material_reads(self):
        reads = material_reads(mesh_ir())
        self.assertEqual([(r['ssa'], r['register']) for r in reads], [('%load', 3)])
        self.assertEqual(reads[0]['extracts'], [{'ssa': '%field', 'lane': 1, 'offset': 52}])

    def test_other_element_or_buffer_is_still_rejected(self):
        for ir in (mesh_ir(element=48), mesh_ir(buffer='CulledMeshlet')):
            with self.subTest(), self.assertRaisesRegex(ValueError, '未知材质表基址'):
                material_reads(ir)

    def test_chain_mixed_with_other_value_stays_a_material_read(self):
        reads = material_reads(mesh_ir(mixed=True))
        self.assertEqual(sorted(r['ssa'] for r in reads), ['%load', '%rec'])


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

    def test_unsuffixed_reserve_member_is_tolerated_like_numbered_reserves(self):
        # RE4 模板同时有 CAPCOM_MATERIAL_RESERVE 与 CAPCOM_MATERIAL_RESERVE0..2；原版 MDF 都不写这些保留项。
        doc = MdfDocument(mdf_fixture(), Path('synthetic.mdf2.32'))
        for reserve in ('CAPCOM_MATERIAL_RESERVE', 'CAPCOM_MATERIAL_RESERVE1'):
            info = {'source': str(RE4_PATH), 'metadata': {'cbuffers': [{'name': 'UserMaterial', 'buffer_size': 16, 'members': [
                {'name': 'Value', 'byte_offset': 0, 'byte_size': 4}, {'name': reserve, 'byte_offset': 4, 'byte_size': 4}]}],
                'srvs': [{'name': 'BaseMap'}]}}
            with self.subTest(reserve=reserve):
                self.assertEqual(doc.binding_report(0, info)['status'], 'compatible_with_reserves')
        info['metadata']['cbuffers'][0]['members'][1]['name'] = 'CAPCOM_MATERIAL_RESERVED'
        self.assertEqual(doc.binding_report(0, info)['status'], 'review_required')

    def test_unregistered_mdf_version_is_rejected(self):
        with self.assertRaisesRegex(MdfError, '2.32'):
            MdfDocument(mdf_fixture(), Path('synthetic.mdf2.50'))


if __name__ == '__main__':
    unittest.main()
