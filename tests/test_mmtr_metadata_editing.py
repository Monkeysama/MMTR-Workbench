"""MMTR cbuffer metadata 增删的离线结构回读测试。"""
from pathlib import Path
import tempfile
import unittest

from inspect_mmtr import inspect_data
from mmtr_editor.dxc import Dxc
from mmtr_editor.model import Document, EditError
from mmtr_editor.rpc import Service


MMTR = Path(r"F:\MH Wilds\Game Unpack\MHWILDS_EXTRACT\natives\STM\MaterialShader\Variation\VFX_Transparent_SFC_Hand.mmtr.240906211")
DXC = Path(r"C:\Software\Kiro\dxcompiler.dll")


@unittest.skipUnless(MMTR.is_file(), "本地 VFX Transparent MMTR 语料不可用")
class MmtrMetadataEditingTests(unittest.TestCase):
    def test_add_user_material_member_relocates_prefix_and_preserves_blobs(self):
        document = Document.open(MMTR)
        original = document.info
        document.add_cbuffer_member("UserMaterial", "CodexParam", 4, 64)
        data, report = document.build()
        _, parsed = inspect_data(data, MMTR)
        rows = [row for row in parsed["metadata"]["cbuffers"] if row["name"] == "UserMaterial"]
        self.assertTrue(rows)
        self.assertTrue(all(row["member_count"] == 8 and row["buffer_size"] == 80 for row in rows))
        self.assertEqual(rows[0]["members"][-1]["name"], "CodexParam")
        self.assertEqual(rows[0]["members"][-1]["byte_offset"], 64)
        self.assertTrue(report["metadata_edits"])
        for old, new in zip(original["shaders"], parsed["shaders"]):
            self.assertEqual(data[new["offset"]:new["trailer_offset"]], document.shader_bytes(old["index"]))

    def test_remove_user_material_member_updates_all_shared_descriptors(self):
        document = Document.open(MMTR)
        document.remove_cbuffer_member("UserMaterial", "VolumeBlend")
        data, _report = document.build()
        _, parsed = inspect_data(data, MMTR)
        rows = [row for row in parsed["metadata"]["cbuffers"] if row["name"] == "UserMaterial"]
        self.assertTrue(all(row["member_count"] == 6 and row["buffer_size"] == 64 for row in rows))
        self.assertNotIn("VolumeBlend", {member["name"] for member in rows[0]["members"]})

    def test_invalid_member_edit_does_not_mutate_document(self):
        document = Document.open(MMTR)
        with self.assertRaises(EditError):
            document.add_cbuffer_member("UserMaterial", "ColorParam", 4, 64)
        with self.assertRaises(EditError):
            document.add_cbuffer_member("UserMaterial", "BadOffset", 4, 60)
        self.assertFalse(document.dirty)
        self.assertEqual(document.build()[0], document.original)

    def test_rpc_accepts_parameter_size_fields_from_frontend(self):
        service = Service()
        service.doc = Document.open(MMTR)
        snapshot = service.handle({
            "op": "mmtr_add_cbuffer_member",
            "cbuffer": "UserMaterial",
            "name": "RpcParam",
            "byteSize": 4,
            "byteOffset": 64,
        })
        self.assertEqual(snapshot["changes"]["metadata"], 1)
        self.assertIn("RpcParam", {row["name"] for row in snapshot["metadata"] if row["kind"] == "member"})

    def test_add_and_remove_srv_resource_relocates_shared_table(self):
        document = Document.open(MMTR)
        document.add_resource("srvs", 4, "CodexTexture")
        data, _report = document.build()
        _, parsed = inspect_data(data, MMTR)
        self.assertIn("CodexTexture", {row["name"] for row in parsed["metadata"]["srvs"]})
        self.assertEqual(len(parsed["records"][4]["metadata_tables"]["srvs"]), 2)
        edited = Document(data, MMTR)
        edited.remove_resource("srvs", 4, "CodexTexture")
        restored, _report = edited.build()
        _, restored_info = inspect_data(restored, MMTR)
        self.assertEqual(len(restored_info["records"][4]["metadata_tables"]["srvs"]), 1)

    @unittest.skipUnless(DXC.is_file(), "本地 DXC 不可用")
    def test_interface_validation_rejects_metadata_only_parameter(self):
        document = Document.open(MMTR)
        document.add_cbuffer_member("UserMaterial", "CodexParam", 4, 64)
        report = document.validate_metadata_interfaces(DXC)
        self.assertFalse(report["valid"])
        self.assertTrue(any(issue["kind"] == "cbuffer" for issue in report["issues"]))

    @unittest.skipUnless(DXC.is_file(), "本地 DXC 不可用")
    def test_interface_validation_rejects_shared_resource_not_declared_by_all_users(self):
        document = Document.open(MMTR)
        # 记录 34 的像素 Shader 已声明 BilinearClamp，但原记录表没有该项。
        document.add_resource("samplers", 34, "BilinearClamp")
        report = document.validate_metadata_interfaces(DXC)
        self.assertFalse(report["valid"])
        self.assertTrue(any(issue["kind"] == "samplers" for issue in report["issues"]))

    @unittest.skipUnless(DXC.is_file(), "本地 DXC 不可用")
    def test_rpc_export_blocks_unverified_metadata(self):
        document = Document.open(MMTR)
        document.add_cbuffer_member("UserMaterial", "CodexParam", 4, 64)
        service = Service()
        service.doc = document
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(EditError):
                service.handle({"op": "export", "path": str(Path(directory) / "edited.mmtr"), "dll": str(DXC)})

    @unittest.skipUnless(DXC.is_file(), "本地 DXC 不可用")
    def test_sync_compiled_interface_stages_tail_member_and_shader_atomically(self):
        source = (
            "cbuffer UserMaterial : register(b2) { "
            "float4 ColorParam; float4 ColorA; float4 ColorB; float DepthBias; "
            "float EmissiveIntensity; float EmissivePower; float VolumeBlend; float CodexParam; }; "
            "struct I { float3 p : POSITION; }; "
            "float4 main(I i) : SV_Position { return float4(i.p, 1) + ColorParam * 0 + CodexParam; }"
        )
        decoder = Dxc(DXC)
        try:
            payload, _ = decoder.compile(source, "main", "vs_6_2")
        finally:
            decoder.close()
        document = Document.open(MMTR)
        report = document.sync_compiled_interface(4, payload, DXC)
        self.assertEqual(report["shader_index"], 4)
        self.assertEqual(report["metadata_operations"][0]["name"], "CodexParam")
        self.assertEqual(document.shader_bytes(4), payload)
        self.assertTrue(any(edit["kind"] == "add_cbuffer_member" for edit in document.metadata_edits))

    @unittest.skipUnless(DXC.is_file(), "本地 DXC 不可用")
    def test_batch_sync_reports_missing_variants_without_mutating_document(self):
        source = (
            "cbuffer UserMaterial : register(b2) { "
            "float4 ColorParam; float4 ColorA; float4 ColorB; float DepthBias; "
            "float EmissiveIntensity; float EmissivePower; float VolumeBlend; float CodexParam; }; "
            "struct I { float3 p : POSITION; }; "
            "float4 main(I i) : SV_Position { return float4(i.p, 1) + ColorParam * 0 + CodexParam; }"
        )
        decoder = Dxc(DXC)
        try:
            payload, _ = decoder.compile(source, "main", "vs_6_2")
        finally:
            decoder.close()
        document = Document.open(MMTR)
        original_bytes = document.shader_bytes(4)
        report = document.sync_compiled_interfaces({4: payload}, DXC)
        self.assertFalse(report["complete"])
        self.assertFalse(report["applied"])
        self.assertIn(4, report["submitted_shader_indices"])
        self.assertTrue(report["missing_shader_indices"])
        self.assertEqual(document.shader_bytes(4), original_bytes)
        self.assertFalse(document.metadata_edits)


if __name__ == "__main__":
    unittest.main()
