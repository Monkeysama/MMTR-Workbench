"""Explicit, non-destructive MDF/MMTR template migration diagnostics."""
from __future__ import annotations

from typing import Any
import re

from .mdf import user_material_layout, master_matches, MdfError


def _user_material(info: dict[str, Any]) -> dict[str, Any]:
    return user_material_layout(info)


def _srvs(info: dict[str, Any]) -> set[str]:
    return {x["name"] for x in info.get("metadata", {}).get("srvs", []) if x.get("name")}


def build_migration_report(material: dict[str, Any], source_info: dict[str, Any], target_info: dict[str, Any]) -> dict[str, Any]:
    """Compare one MDF material against a source and target MMTR layout.

    This function never creates an MDF.  It classifies whether a future
    migration can copy a value by name, needs a target default, drops a source
    feature, or requires structural repacking of the MDF property block.
    """
    source_cb = _user_material(source_info)
    target_cb = _user_material(target_info)
    if not master_matches(material['master'], source_info.get('source', '')):
        raise MdfError('源 MMTR 路径与所选 MDF 材质的 master 不对应。')
    source_members = {x["name"]: x for x in source_cb.get("members", [])}
    target_members = {x["name"]: x for x in target_cb.get("members", [])}
    mdf_props = {x["name"]: x for x in material.get("properties", [])}
    if len(mdf_props) != len(material.get("properties", [])):
        raise MdfError('MDF 包含重复属性名，无法按名称生成唯一映射。')
    source_names, target_names = set(source_members), set(target_members)
    mapping = []
    for name in sorted(target_names):
        source = source_members.get(name)
        target = target_members[name]
        prop = mdf_props.get(name)
        if prop is None and re.fullmatch(r'CAPCOM_MATERIAL_RESERVE\d+', name):
            status = 'reserve_value_unspecified'
        elif source is None:
            status = "target_default_required"
        elif prop is None:
            status = "source_value_missing"
        elif prop["offset"] != source["byte_offset"] or prop["byte_size"] != source["byte_size"]:
            status = "source_binding_mismatch"
        elif source["byte_size"] != target["byte_size"]:
            status = "width_conflict"
        elif source["byte_offset"] != target["byte_offset"]:
            status = "copy_requires_repack"
        else:
            status = "copy_in_place"
        mapping.append({
            "name": name,
            "status": status,
            "source_mmtr_offset": source["byte_offset"] if source else None,
            "target_mmtr_offset": target["byte_offset"],
            "byte_size": target["byte_size"],
            "source_byte_size": source["byte_size"] if source else None,
            "mdf_offset": prop["offset"] if prop else None,
            "mdf_byte_size": prop["byte_size"] if prop else None,
            "source_values": prop.get("values") if prop else None,
            "source_raw_hex": prop.get("raw_hex") if prop else None,
        })
    source_only = [
        {"name": name, "source_offset": source_members[name]["byte_offset"], "byte_size": source_members[name]["byte_size"], "status": "source_feature_not_in_target"}
        for name in sorted(source_names - target_names)
    ]
    source_srvs, target_srvs = _srvs(source_info), _srvs(target_info)
    extra_mdf_properties = [
        {"name": name, "mdf_offset": prop["offset"], "byte_size": prop["byte_size"], "status": "mdf_property_not_in_source"}
        for name, prop in sorted(mdf_props.items()) if name not in source_names
    ]
    texture_mapping = []
    mdf_texture_names = {x["name"] for x in material.get("textures", [])}
    # MMTR metadata contains hundreds of engine-global SRVs.  Only MDF's
    # declared texture names are material-slot candidates; comparing every SRV
    # would falsely report global lighting resources as missing MDF textures.
    for name in sorted(mdf_texture_names):
        if name not in source_srvs:
            status = "source_mdf_slot_not_in_source_mmtr"
        elif name in target_srvs:
            status = "copy_slot"
        else:
            status = "source_texture_not_supported"
        paths = [x["path"] for x in material.get("textures", []) if x["name"] == name]
        texture_mapping.append({"name": name, "status": status, "in_mdf": True, "source_paths": paths})
    structural_repack = any(x["status"] == "copy_requires_repack" for x in mapping)
    blockers = sorted({
        *(x["status"] for x in mapping if x["status"] in {"target_default_required", "source_value_missing", "source_binding_mismatch", "width_conflict"}),
        *(x["status"] for x in texture_mapping if x["status"] in {"source_texture_not_supported", "source_mdf_slot_not_in_source_mmtr"}),
        *("source_feature_not_in_target" for x in source_only),
        *("mdf_property_not_in_source" for x in extra_mdf_properties),
    })
    return {
        "material_index": material["index"],
        "material_name": material["name"],
        "source_master": material["master"],
        "source_mmtr": {"source": source_info.get("source", ""), "sha256": source_info.get("sha256", ""), "user_material_size": source_cb.get("buffer_size")},
        "target_mmtr": {"source": target_info.get("source", ""), "sha256": target_info.get("sha256", ""), "user_material_size": target_cb.get("buffer_size")},
        "property_mapping": mapping,
        "source_only_properties": source_only,
        "extra_mdf_properties": extra_mdf_properties,
        "texture_mapping": texture_mapping,
        "structural_mdf_repack_required": structural_repack,
        "blockers": blockers,
        "safe_to_auto_migrate": False,
        "scope": "metadata_layout_only",
        "semantic_equivalence_verified": False,
        "target_material_texture_requirements": "unknown_until_target_mdf_or_shader_binding_analysis",
        "engine_resource_changes": {'added_srvs': sorted(target_srvs-source_srvs), 'removed_srvs': sorted(source_srvs-target_srvs)},
        "limitations": ['同名且等宽只代表可搬运字节，须核对 Shader 语义、采样和运行时路由。', '保留成员缺省仅列出，未推断默认值。', '目标全部材质贴图需求需要目标 MDF 或逐入口绑定证据，不能把引擎全局 SRV 当成材质贴图。'],
        "summary": {
            "copy_in_place": sum(x["status"] == "copy_in_place" for x in mapping),
            "copy_requires_repack": sum(x["status"] == "copy_requires_repack" for x in mapping),
            "target_default_required": sum(x["status"] == "target_default_required" for x in mapping),
            "reserve_value_unspecified": sum(x["status"] == "reserve_value_unspecified" for x in mapping),
            "source_value_missing": sum(x["status"] == "source_value_missing" for x in mapping),
            "source_binding_mismatch": sum(x["status"] == "source_binding_mismatch" for x in mapping),
            "width_conflict": sum(x["status"] == "width_conflict" for x in mapping),
            "mdf_property_not_in_source": len(extra_mdf_properties),
            "source_feature_not_in_target": len(source_only),
            "source_texture_not_supported": sum(x["status"] == "source_texture_not_supported" for x in texture_mapping),
            "copy_slot": sum(x["status"] == "copy_slot" for x in texture_mapping),
            "source_mdf_slot_not_in_source_mmtr": sum(x["status"] == "source_mdf_slot_not_in_source_mmtr" for x in texture_mapping),
        },
        "status_catalog": {
            "copy_in_place": "同名、宽度和偏移一致，仍需确认语义",
            "copy_requires_repack": "同名且宽度一致，但目标偏移不同，需要迁移布局并确认语义",
            "target_default_required": "目标 Shader 有成员，当前 MDF 没有值，必须提供经验证的默认值",
            "reserve_value_unspecified": "MDF 未提供保留成员值，未推断默认值",
            "source_value_missing": "源 Shader 有成员，但 MDF 未提供值",
            "source_binding_mismatch": "MDF 的偏移或宽度与源 Shader 不一致，不能搬运",
            "width_conflict": "源与目标成员宽度不同，不能直接复制",
            "mdf_property_not_in_source": "MDF 属性未在源 Shader 元数据中声明，需核实来源",
            "source_feature_not_in_target": "源材质功能没有目标成员，迁移会丢失该输入",
            "source_texture_not_supported": "源纹理槽在目标 MMTR 中没有对应 SRV",
            "source_mdf_slot_not_in_source_mmtr": "当前 MDF 声明的纹理槽在源 MMTR 元数据中也没有对应 SRV",
            "copy_slot": "同名纹理槽候选，尚未验证采样方式和通道语义",
        },
    }
