"""MDF 2.45 parsing and read-only MDF/MMTR binding diagnostics."""
from pathlib import Path
import tempfile
import unittest

from mmtr_editor.mdf import MdfDocument, MdfError
from mmtr_editor.rpc import Service
from tests.local_corpus import ORIGINAL_TOP_MDF as MDF, verify_original_top_mdf

ROOT = Path(__file__).resolve().parents[1]
MMTR = ROOT / "output/pix/detailcol_reference/archives"


@unittest.skipUnless(MDF.is_file() and MMTR.is_dir(), "local MHWILDS MDF/MMTR corpus unavailable")
class MdfLinkageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        verify_original_top_mdf()
        cls.mdf = MdfDocument.open(MDF)
        cls.mmtr = next(MMTR.rglob("Base_ATOS_Emit_FX_Detail_DetailColLayer.mmtr.240906211"))

    def test_current_mdf_roundtrips_to_expected_top_fields(self):
        self.assertEqual(self.mdf.material_count, 60)
        top = self.mdf.material(6)
        self.assertEqual(top["name"], "CursedVeil_Top")
        self.assertEqual(top["master"], "MaterialShader/Variation/Base_ATOS_Emit_FX_Detail_DetailColLayer.mmtr")
        self.assertEqual(top["property_count"], 166)
        self.assertEqual(top["texture_count"], 22)
        self.assertEqual(next(p for p in top["properties"] if p["name"] == "AlphaAdjust")["offset"], 552)

    def test_binding_report_detects_only_reserve_members_for_original_template(self):
        service = Service()
        opened = service.handle({"op": "mdf_open", "path": str(MDF), "material_index": 6})
        self.assertEqual(opened["binding"]["status"], "mmtr_not_loaded")
        result = service.handle({"op": "mdf_link", "mmtr_path": str(self.mmtr)})
        report = result["binding"]
        self.assertTrue(result["selected"]["mmtr_linked"])
        self.assertTrue(result["materials"][6]["mmtr_linked"])
        self.assertEqual(report["mmtr"]["user_material_size"], 960)
        self.assertEqual(report["status"], "compatible_with_reserves")
        self.assertEqual(sum(row["status"] == "match" for row in report["properties"]), 166)
        self.assertEqual([row["name"] for row in report["properties"] if row["status"] != "match"], ["CAPCOM_MATERIAL_RESERVE0", "CAPCOM_MATERIAL_RESERVE1"])
        self.assertEqual([row["name"] for row in report["missing_mdf_properties"]], ["CAPCOM_MATERIAL_RESERVE0", "CAPCOM_MATERIAL_RESERVE1"])
        self.assertEqual(report["extra_mdf_properties"], [])

    def test_truncated_mdf_is_rejected(self):
        with self.assertRaises(MdfError):
            MdfDocument(self.mdf.original[:100])

    def test_property_edit_changes_only_existing_float_bytes_and_reparses(self):
        service = Service()
        service.handle({"op": "mdf_open", "path": str(MDF), "material_index": 6})
        changed = service.handle({"op": "mdf_set_property", "material_index": 6, "name": "AlphaAdjust", "values": [1.0]})
        self.assertTrue(changed["dirty"])
        self.assertEqual(next(p for p in changed["selected"]["properties"] if p["name"] == "AlphaAdjust")["values"], [1.0])
        data, report = service.mdf.build()
        self.assertTrue(report["byte_length_unchanged"])
        self.assertTrue(report["unknown_bytes_preserved"])
        self.assertEqual(sum(report["byte_ranges"][i]["size"] for i in range(len(report["byte_ranges"]))), 4)
        reopened = MdfDocument(data)
        self.assertEqual(next(p for p in reopened.material(6)["properties"] if p["name"] == "AlphaAdjust")["values"], [1.0])
        self.assertEqual(service.mdf.original, MDF.read_bytes())

    def test_export_refuses_source_overwrite_and_writes_scoped_copy(self):
        service = Service()
        service.handle({"op": "mdf_open", "path": str(MDF), "material_index": 6})
        service.handle({"op": "mdf_set_property", "material_index": 6, "name": "AlphaAdjust", "values": [0.25]})
        with self.assertRaises(Exception):
            service.handle({"op": "mdf_export", "path": str(MDF)})
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "edited.mdf2.45"
            result = service.handle({"op": "mdf_export", "path": str(out)})
            self.assertTrue(out.is_file())
            self.assertEqual(result["size"], MDF.stat().st_size)
            self.assertEqual(next(p for p in MdfDocument.open(out).material(6)["properties"] if p["name"] == "AlphaAdjust")["values"], [0.25])


if __name__ == "__main__":
    unittest.main()
