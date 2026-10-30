"""SF6（MMTR 221102761/250109895、MDF 2.31）与旧版 DD2（0x108）的识别和组数约束；只用合成数据。"""
from pathlib import Path
import unittest

from inspect_mmtr import inspect_data
from mmtr_editor.mdf import MdfDocument, MdfError
from mmtr_editor.model import Document, EditError
from test_mdf_editing import fixture as mdf_fixture
from test_structure import fixture


def sf6_fixture(groups=3, programs=1):
    return fixture(groups, programs, record_size=0x108)


SF6_PATHS = {
    221102761: Path('product/mastermaterial/character/synthetic.mmtr.221102761'),
    250109895: Path('product/mastermaterial/character/synthetic.mmtr.250109895'),
}
DD2_PATHS = {
    240112003: Path('product/mastermaterial/synthetic.mmtr.240112003'),
    240423143: Path('product/mastermaterial/synthetic.mmtr.240423143'),
}


class Sf6MmtrTests(unittest.TestCase):
    def test_both_resource_versions_select_sf6_layout(self):
        for version, path in SF6_PATHS.items():
            with self.subTest(version=version):
                _, info = inspect_data(sf6_fixture(), path)
                self.assertEqual((info['game'], info['record_size'], info['resource_version']), ('SF6', 0x108, version))
                self.assertEqual((info['record_count'], info['record_end']), (3, 16+3*0x108))

    def test_wrong_game_suffix_is_rejected_by_document(self):
        with self.assertRaises(EditError):
            Document(sf6_fixture(), Path('product/mastermaterial/synthetic.mmtr.240906211'))
        with self.assertRaises(EditError):
            Document(sf6_fixture(4), SF6_PATHS[221102761])

    def test_metadata_edit_keeps_sf6_record_size(self):
        doc = Document(sf6_fixture(3, 2), SF6_PATHS[221102761])
        doc.add_cbuffer_member('UserMaterial', 'Added', 4, 16)
        rebuilt = doc.build()[0]
        _, info = inspect_data(rebuilt, SF6_PATHS[221102761])
        self.assertEqual((info['game'], info['record_size']), ('SF6', 0x108))
        self.assertEqual([m['name'] for m in info['metadata']['cbuffers'][0]['members']], ['Value', 'Added'])


class Dd2MmtrTests(unittest.TestCase):
    def test_old_dd2_resource_versions_use_three_group_layout(self):
        for version, path in DD2_PATHS.items():
            with self.subTest(version=version):
                doc = Document(sf6_fixture(), path)
                self.assertEqual((doc.info['game'], doc.info['record_size']), ('DD2', 0x108))
                with self.assertRaisesRegex(EditError, '旧版 DD2 三组'):
                    Document(sf6_fixture(4), path)


class Sf6MdfTests(unittest.TestCase):
    def test_mdf231_opens_with_sf6_master_root(self):
        doc = MdfDocument(mdf_fixture(), Path('synthetic.mdf2.31'))
        self.assertEqual((doc.version, doc.game, doc.snapshot()['game']), (31, 'SF6', 'SF6'))
        doc.set_master(0, 'product/mastermaterial/character/test.mmtr')
        reopened = MdfDocument(doc.build()[0], Path('synthetic.mdf2.31'))
        self.assertEqual(reopened.material(0)['master'], 'product/mastermaterial/character/test.mmtr')
        with self.assertRaisesRegex(MdfError, 'product/mastermaterial/'):
            doc.set_master(0, '_Chainsaw/MasterMaterial/test.mmtr')
        with self.assertRaisesRegex(MdfError, 'product/mastermaterial/'):
            doc.set_master(0, 'MaterialShader/test.mmtr')
        wilds = MdfDocument(mdf_fixture(), Path('synthetic.mdf2.45'))
        with self.assertRaisesRegex(MdfError, 'MaterialShader/'):
            wilds.set_master(0, 'product/mastermaterial/test.mmtr')

    def test_unregistered_mdf_version_message_lists_sf6_layout(self):
        with self.assertRaisesRegex(MdfError, r'2\.31.*2\.32'):
            MdfDocument(mdf_fixture(), Path('synthetic.mdf2.50'))


if __name__ == '__main__':
    unittest.main()
