"""Read-only SDF/MMTR inspection for the registered RE Engine shader families (see FORMATS).

The SDF record layout is inferred from shipped files, not an official schema.
DXBC/DXIL structures are decoded independently and bounds checked.
"""

import argparse
import collections
import csv
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import uuid


STAGES = {0: "ps", 1: "vs", 2: "gs", 3: "hs", 4: "ds", 5: "cs", 6: "lib", 13: "ms", 14: "as"}
RECORD_START = 0x10
RECORD_SIZE = 0x110
# 资源版本（文件名后缀）→ (游戏, 记录长度, 已核对的组数, Shader/状态后是否带 8 字节运行时缓存键,
# header 布局)。两种记录在 0xFF 之前逐字段相同；荒野在 0x100..0x107 多出 8 字节（全部原版语料为零），
# 其后的 compute 线程组与拓扑随之后移。RE8/MHRISE/RE2RT 不写运行时缓存键，Shader 记录首尾相接。
# Tier D 的 RE9/Pragmata/DD2/鬼武者使用每目标一个 u16 程序数的 per-target header。
FORMATS = {240906211: ("MHWilds", 0x110, (4, 5), True, "uniform"), 221007879: ("RE4", 0x108, (3,), True, "uniform"),
           221102761: ("SF6", 0x108, (3,), True, "uniform"), 250109895: ("SF6", 0x108, (3,), True, "uniform"),
           240112003: ("DD2", 0x108, (3,), True, "uniform"), 240423143: ("DD2", 0x108, (3,), True, "uniform"),
           2102188797: ("RE8", 0x108, (5,), False, "uniform"), 220427553: ("MHRISE", 0x108, (3,), False, "uniform"),
           2109101635: ("RE2RT", 0x108, (5,), False, "uniform"),
           1808160001: ("RE2", 0x108, (4,), False, "uniform"), 1905100741: ("RE3", 0x108, (4,), False, "uniform"),
           251112994: ("RE9", 0x110, (4,), True, "per-target"),
           251112995: ("PRAGMATA", 0x110, (4,), True, "per-target"),
           251112814: ("ONIMUSHAWOTSDEMO", 0x110, (4,), True, "per-target"),
           260511270: ("DD2", 0x110, (4,), True, "per-target"),
           260605155: ("ONIMUSHAWOTS", 0x110, (4,), True, "per-target")}
# 材质数据（BindlessBuffer 与 heap CBV）中 UserMaterial 之前的字节数。鬼武者第 0 行 lane 0 为模板 ID 标记，
# 参数从第 1 行开始；荒野、RE4 及其余已登记游戏从第 0 行开始。参数实例流与具名 UserMaterial 不含该行。
MATERIAL_HEADER_BYTES = {"ONIMUSHAWOTS": 16, "ONIMUSHAWOTSDEMO": 16}
TABLE_FIELDS = {"cbuffers": (0x70, 0xF6, 32), "samplers": (0x80, 0xF7, 16),
                "srvs": (0x90, 0xFC, 16), "uavs": (0xA0, 0xFD, 16)}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def unpack(data, fmt, offset):
    if offset < 0 or offset + struct.calcsize(fmt) > len(data):
        raise ValueError(f"Out-of-bounds {fmt} at {offset:#x}")
    return struct.unpack_from(fmt, data, offset)


def u32(data, offset):
    return unpack(data, "<I", offset)[0]


def u64(data, offset):
    return unpack(data, "<Q", offset)[0]


def cstring(data, offset, end):
    if not 0 < offset < end:
        return None
    stop = data.find(b"\0", offset, min(end, offset + 512))
    if stop <= offset or any(not 32 <= c < 127 for c in data[offset:stop]):
        return None
    return data[offset:stop].decode("ascii")


def decode_dxbc(data, offset):
    if data[offset:offset + 4] != b"DXBC":
        raise ValueError(f"No DXBC container at {offset:#x}")
    version, size, count = unpack(data, "<III", offset + 20)
    if version != 1 or count > 32 or size < 32 + count * 4 or offset + size > len(data):
        raise ValueError(f"Invalid DXBC header at {offset:#x}")
    chunks, ranges = [], []
    for i in range(count):
        relative = u32(data, offset + 32 + i * 4)
        if relative < 32 + count * 4 or relative + 8 > size:
            raise ValueError("DXBC chunk table overlaps header or exceeds container")
        start = offset + relative
        tag = data[start:start + 4].decode("ascii")
        length = u32(data, start + 4)
        if relative + 8 + length > size:
            raise ValueError("DXBC chunk exceeds container")
        ranges.append((relative, relative + 8 + length))
        chunks.append({"tag": tag, "offset": start, "relative_offset": relative, "size": length})
    ordered = sorted(ranges)
    if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
        raise ValueError("Overlapping DXBC chunks")
    dxil = next((c for c in chunks if c["tag"] == "DXIL"), None)
    if dxil is None:
        raise ValueError("This inspector expects DXIL containers")
    start = dxil["offset"] + 8
    program_version, words, magic, dxil_version, bitcode_relative, bitcode_size = unpack(data, "<6I", start)
    bitcode = start + 8 + bitcode_relative
    if magic != 0x4C495844 or words * 4 != dxil["size"]:
        raise ValueError("Invalid DXIL program header")
    if bitcode < start + 24 or bitcode + bitcode_size > start + dxil["size"]:
        raise ValueError("DXIL bitcode out of bounds")
    if data[bitcode:bitcode + 4] != b"BC\xc0\xde":
        raise ValueError("Invalid LLVM bitcode signature")
    kind = program_version >> 16
    model = f"{(program_version >> 4) & 15}.{program_version & 15}"
    return {
        "offset": offset, "size": size, "sha256": sha256(data[offset:offset + size]),
        "stage": STAGES.get(kind, str(kind)), "shader_model": model,
        "dxil_version": f"{dxil_version >> 8}.{dxil_version & 255}",
        "bitcode_offset": bitcode, "bitcode_size": bitcode_size,
        "container_hash_raw": data[offset + 4:offset + 20].hex(), "chunks": chunks,
    }


def inspect(path):
    data = path.read_bytes()
    return inspect_data(data, path)


def resource_version(path):
    match = re.search(r"\.mmtr\.(\d+)$", str(path), re.I)
    return int(match[1]) if match else None


def inspect_data(data, path):
    """Inspect immutable in-memory bytes; also used to verify rebuilt editor output.

    记录长度按文件名的资源版本选择；无后缀或未登记版本时，只接受恰好一种已知长度能完整解析的文件。
    """
    version = resource_version(path)
    if version in FORMATS:
        game, record_size, _groups, runtime_keys, header_layout = FORMATS[version]
        return _inspect_layout(data, path, game, record_size, version, runtime_keys, header_layout)
    results, errors = [], []
    seen = set()
    for game, record_size, _groups, runtime_keys, header_layout in FORMATS.values():
        # 无后缀时按“记录长度 + 缓存键布局 + header 布局”去重；不同布局不能共用同一次解析。
        key = (record_size, runtime_keys, header_layout)
        if key in seen:
            continue
        seen.add(key)
        try:
            results.append(_inspect_layout(data, path, game, record_size, version, runtime_keys, header_layout))
        except ValueError as exc:
            errors.append(exc)
    if len(results) == 1:
        return results[0]
    if not results:
        raise errors[0]
    raise ValueError("SDF record size is ambiguous; add the resource version suffix to the file name")


def _inspect_layout(data, path, game, record_size, version, runtime_cache_keys,
                    header_layout="uniform"):
    if data[:4] != b"SDF\0":
        raise ValueError("Not an SDF/MMTR file")
    header04, header06 = unpack(data, "<HH", 4)
    if header_layout == "uniform":
        program_counts = [header06] * header04
        record_start = RECORD_START
        shader_start = u64(data, 8)
    elif header_layout == "per-target":
        counts_end = 6 + header04 * 2
        if counts_end > len(data):
            raise ValueError("Per-target header is truncated")
        header_qword = (counts_end + 7) & ~7
        if header_qword + 8 > len(data):
            raise ValueError("Per-target header is truncated")
        if any(data[counts_end:header_qword]):
            raise ValueError("Per-target header padding is non-zero")
        program_counts = [unpack(data, "<H", 6 + i * 2)[0] for i in range(header04)]
        if sum(program_counts) and any(count <= 0 for count in program_counts):
            raise ValueError("Every declared target must own at least one program")
        shader_start = u64(data, header_qword)
        record_start = header_qword + 8
    else:
        raise ValueError(f"Unknown SDF header layout: {header_layout}")
    if not record_start < shader_start < len(data):
        raise ValueError("Invalid shader region start")
    shaders = []
    offset = shader_start
    while offset < len(data):
        shader = decode_dxbc(data, offset)
        shader["index"] = len(shaders)
        trailer = offset + shader["size"]
        shader["trailer_offset"] = trailer
        if runtime_cache_keys:
            if trailer + 8 > len(data):
                raise ValueError("Missing eight-byte container trailer")
            shader["trailer_hex"] = data[trailer:trailer + 8].hex()
            offset = trailer + 8
        else:
            shader["trailer_hex"] = ""
            offset = trailer
        shaders.append(shader)
    by_offset = {s["offset"]: s for s in shaders}
    metadata = {kind: {} for kind in TABLE_FIELDS}

    def metadata_entry(kind, position):
        if position in metadata[kind]:
            return metadata[kind][position]
        width = TABLE_FIELDS[kind][2]
        if position < record_start or position + width > shader_start:
            raise ValueError("Metadata descriptor outside metadata region")
        nameptr, namehash, reserved = unpack(data, "<QII", position)
        name = cstring(data, nameptr, shader_start)
        if name is None:
            raise ValueError("Invalid metadata name")
        entry = {"offset": position, "name": name, "name_offset": nameptr,
                 "name_hash_raw": namehash, "reserved_raw": reserved}
        if kind == "cbuffers":
            size, count, memberptr = unpack(data, "<IIQ", position + 16)
            if count > 4096 or (count and not RECORD_START <= memberptr < shader_start) or memberptr + count * 16 > shader_start:
                raise ValueError("Invalid constant buffer member table")
            members = []
            for i in range(count):
                pos = memberptr + i * 16
                ptr, hash32, byte_offset, byte_size = unpack(data, "<QIHH", pos)
                member_name = cstring(data, ptr, shader_start)
                if member_name is None or byte_offset + byte_size > size:
                    raise ValueError("Invalid constant buffer member")
                members.append({"offset": pos, "name": member_name, "name_offset": ptr,
                                "name_hash_raw": hash32, "byte_offset": byte_offset, "byte_size": byte_size})
            entry.update(buffer_size=size, member_count=count, members_offset=memberptr, members=members)
        metadata[kind][position] = entry
        return entry

    declared_record_count = sum(program_counts)
    # 记录表由文件头声明的目标计数界定；其后的区域是 metadata/字符串，不能继续按记录扫描。
    if header_layout == "per-target" and record_start + declared_record_count * record_size > shader_start:
        raise ValueError("Header-declared record table is truncated")
    record_limit = (declared_record_count
                    if declared_record_count
                    and record_start + declared_record_count * record_size <= shader_start
                    else None)
    records = []
    offset = record_start
    while (offset + record_size <= shader_start
           and (record_limit is None or len(records) < record_limit)):
        name_pointer = u64(data, offset)
        name = cstring(data, name_pointer, shader_start)
        if name is None:
            break
        references = []
        for relative in range(8, 0x40, 8):
            pointer = u64(data, offset + relative)
            length = u32(data, offset + 0xB8 + (relative - 8) // 2)
            if not pointer:
                if length:
                    raise ValueError("Zero shader pointer with nonzero byte length")
                continue
            if pointer not in by_offset:
                raise ValueError(f"Record {len(records)} has an invalid shader reference at {relative:#x}")
            shader = by_offset[pointer]
            if length != shader["size"]:
                raise ValueError("Pass shader length does not match DXBC header")
            references.append({"field": relative, "shader_index": shader["index"], "offset": pointer,
                               "stage": shader["stage"], "size": length})
        tables = {}
        for kind, (pointer_field, count_field, width) in TABLE_FIELDS.items():
            pointer, count = u64(data, offset + pointer_field), data[offset + count_field]
            if bool(pointer) != bool(count) or (count and pointer + count * width > shader_start):
                raise ValueError("Metadata pointer/count mismatch")
            tables[kind] = [metadata_entry(kind, pointer + i * width)["offset"] for i in range(count)]
        records.append({
            "index": len(records), "offset": offset, "name": name, "name_offset": name_pointer,
            "shader_references": references,
            "metadata_tables": tables,
            "per_stage_counts_raw": {kind: list(data[offset + pos:offset + pos + 6])
                                     for kind, pos in [("cbuffers", 0xD8), ("samplers", 0xDE), ("srvs", 0xE4), ("uavs", 0xEA)]},
            "raw_u32": [u32(data, offset + i) for i in range(0, record_size, 4)],
        })
        offset += record_size
    if record_limit is not None and len(records) != record_limit:
        raise ValueError("Header-declared record table is truncated")
    names = collections.defaultdict(list)
    for record in records:
        for ref in record["shader_references"]:
            if record["name"] not in names[ref["shader_index"]]:
                names[ref["shader_index"]].append(record["name"])
    for shader in shaders:
        shader["pass_names"] = names[shader["index"]]
    groups = []
    if sum(program_counts) == len(records):
        first_names = None
        cursor = 0
        for i, count in enumerate(program_counts):
            group = records[cursor:cursor + count]
            ids = sorted({ref["shader_index"] for record in group for ref in record["shader_references"]})
            names = [r["name_offset"] for r in group]
            if first_names is None:
                first_names = names
            for record in group:
                record["group_index"] = i
            groups.append({"index": i, "record_count": len(group), "unique_shader_count": len(ids),
                           "stages": dict(collections.Counter(shaders[j]["stage"] for j in ids)),
                           "shader_models": dict(collections.Counter(shaders[j]["shader_model"] for j in ids)),
                           "name_sequence_matches_first": names == first_names})
            cursor += count
    rebuilt = data[:shader_start] + b"".join(
        data[s["offset"]:s["trailer_offset"] + (8 if runtime_cache_keys else 0)] for s in shaders)
    strings = [{"offset": m.start(), "text": m.group().decode("ascii")} for m in re.finditer(rb"[ -~]{5,}", data[:shader_start])]
    return data, {
        "source": str(path.resolve()), "size": len(data), "sha256": sha256(data),
        "magic": "SDF\\0", "header_u32_04": u32(data, 4),
        "header_u16_04": header04, "header_u16_06": header06, "program_counts": program_counts,
        "header_layout": header_layout, "candidate_groups": groups,
        "game": game, "resource_version": version, "runtime_cache_keys": runtime_cache_keys,
        "shader_start": shader_start, "record_start": record_start, "header_size": record_start,
        "shader_start_field": record_start - 8, "record_size": record_size,
        "material_header_bytes": MATERIAL_HEADER_BYTES.get(game, 0),
        "record_end": offset, "record_count": len(records), "shader_count": len(shaders),
        "stages": dict(collections.Counter(s["stage"] for s in shaders)),
        "shader_models": dict(collections.Counter(s["shader_model"] for s in shaders)),
        "string_candidates": strings, "records": records, "shaders": shaders,
        "metadata": {kind: sorted(entries.values(), key=lambda e: e["offset"]) for kind, entries in metadata.items()},
        "validation": {"shader_lengths_match": True, "metadata_ranges_valid": True,
                       "whole_file_identity_rebuild": rebuilt == data,
                       "rebuilt_sha256": sha256(rebuilt)},
        "limitations": ["SDF fields outside the verified record references remain inferred/unknown.",
                        "No runtime shader selection, effect, or game patch was tested.",
                        "Disassembly is compiled DXIL IR, not original HLSL source."]
    }


class GUID(ctypes.Structure):
    _fields_ = [("raw", ctypes.c_ubyte * 16)]


class DxcBuffer(ctypes.Structure):
    _fields_ = [("ptr", ctypes.c_void_p), ("size", ctypes.c_size_t), ("encoding", ctypes.c_uint32)]


def guid(text):
    return GUID((ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(text).bytes_le))


class DxcDisassembler:
    """Owned IDxcCompiler3: decode input bytes without executing shader code."""

    def __init__(self, dll):
        if os.name != "nt":
            raise RuntimeError("The optional DXC DLL bridge requires Windows")
        self.dll_path = Path(dll).resolve()
        self.dll = ctypes.WinDLL(str(self.dll_path))
        self.dll.DxcCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        self.dll.DxcCreateInstance.restype = ctypes.c_int32
        self.compiler = ctypes.c_void_p()
        self.check(self.dll.DxcCreateInstance(
            ctypes.byref(guid("73e22d93-e6ce-47f3-b5bf-f0664f39c1b0")),
            ctypes.byref(guid("228b4687-5a6a-4730-900c-9702b2203f54")), ctypes.byref(self.compiler)))

    @staticmethod
    def check(hr):
        if hr < 0:
            raise RuntimeError(f"DXC HRESULT {hr & 0xffffffff:#010x}")

    @staticmethod
    def method(obj, index, result, *types):
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *types)(vtable[index])

    def release(self, obj):
        if obj:
            self.method(obj, 2, ctypes.c_uint32)(obj)

    def close(self):
        self.release(self.compiler)
        self.compiler = None

    def disassemble(self, data):
        source = ctypes.create_string_buffer(data)
        buffer = DxcBuffer(ctypes.cast(source, ctypes.c_void_p), len(data), 0)
        result, blob = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            self.check(self.method(self.compiler, 4, ctypes.c_int32, ctypes.POINTER(DxcBuffer), ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
                self.compiler, ctypes.byref(buffer), ctypes.byref(guid("58346cda-dde7-4497-9461-6f87af5e0659")), ctypes.byref(result)))
            status = ctypes.c_int32()
            self.check(self.method(result, 3, ctypes.c_int32, ctypes.POINTER(ctypes.c_int32))(result, ctypes.byref(status)))
            self.check(status.value)
            self.check(self.method(result, 4, ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p))(result, ctypes.byref(blob)))
            pointer = self.method(blob, 3, ctypes.c_void_p)(blob)
            size = self.method(blob, 4, ctypes.c_size_t)(blob)
            return ctypes.string_at(pointer, size).rstrip(b"\0").decode("utf-8")
        finally:
            self.release(blob)
            self.release(result)


def reflected_struct_members(body):
    """Recover nested DXC comment fields; offsets remain absolute from $Element.

    Return None for incomplete/unknown trees, so reconstruction fails closed.
    Keep the legacy flat variables alongside this tree for inspection callers.
    """
    stack, root = [], None
    for line in body.splitlines():
        opening = re.fullmatch(r';\s+struct (?:hostlayout\.)?struct\.(\w+)\s*',line)
        if opening:
            stack.append({'struct_type':opening[1], 'members':[]})
            continue
        closing = re.fullmatch(r';\s+} (\$Element|\w+(?:\[\d+\])?);\s+; Offset:\s*(\d+)(?: Size:\s*(\d+))?\s*',line)
        if closing:
            if not stack:return None
            node=stack.pop();node.update(name=closing[1],offset=int(closing[2]))
            if stack:
                stack[-1]['members'].append(node)
            elif closing[1]=='$Element' and root is None:
                root=node
            else:return None
            continue
        variable=re.fullmatch(r';\s+(.+?)\s+(\w+(?:\[\d+\])?);\s+; Offset:\s*(\d+)\s*',line)
        if variable and stack:
            stack[-1]['members'].append({'type':variable[1].strip(),'name':variable[2],'offset':int(variable[3])})
    return root['members'] if root and not stack else None


def reflection(text):
    entry = re.search(r"; EntryFunctionName: (.+)", text)
    if entry is None:
        entry = re.search(r"define void @([^ (]+)", text)
    section = text.split("; Buffer Definitions:", 1)[-1].split("; Resource Bindings:", 1)[0]
    def fields(body):
        variables = []
        for line in body.splitlines():
            # DXC 在长字段名后可能输出 ";; Offset:"；两种分隔都要接受。
            variable = re.search(r";\s+(.+?)\s+(\w+(?:\[[^]]+\])?);+\s*;?\s*Offset:\s*(\d+)", line)
            if variable and not variable[1].strip().startswith("}"):
                variables.append({"type": variable[1].strip(), "name": variable[2], "offset": int(variable[3])})
        return variables

    cbuffers = []
    for match in re.finditer(r"; cbuffer (\S+)(.*?)(?=; cbuffer |; Resource bind info|\Z)", section, re.S):
        size = re.search(r"Size:\s*(\d+)", match[2])
        cbuffers.append({"name": match[1], "size": int(size[1]) if size else None, "variables": fields(match[2])})
    structured = []
    for match in re.finditer(r"; Resource bind info for (\S+)(.*?)(?=; Resource bind info|; cbuffer |\Z)", section, re.S):
        size = re.search(r"\} \$Element;\s+; Offset:\s*0 Size:\s*(\d+)", match[2])
        if size:
            item={'name': match[1], 'size': int(size[1]), 'variables': fields(match[2])}
            members=reflected_struct_members(match[2])
            if members and any('members' in x for x in members):item['members']=members
            structured.append(item)
        else:
            scalar = re.search(r';\s+(\w+) \$Element;\s+; Offset:\s*0 Size:\s*(\d+)', match[2])
            if scalar:
                structured.append({'name': match[1], 'size': int(scalar[2]), 'element_type': scalar[1], 'variables': []})
    resources = []
    binding_section = text.split("; Resource Bindings:", 1)
    if len(binding_section) == 2:
        for line in binding_section[1].split("; ViewId state:", 1)[0].splitlines():
            if re.match(r"; \w+\s+(?:cbuffer|texture|sampler|UAV)", line):
                resources.append(line[2:].strip())
    return {"entry": entry[1] if entry else None, "cbuffers": cbuffers,
            "structured_buffers": structured, "resource_binding_rows": resources}


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dxcompiler", type=Path)
    parser.add_argument("--compare", nargs="*", type=Path, default=[])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    data, result = inspect(args.path)
    export = args.out / "shaders"
    export.mkdir(exist_ok=True)
    decoder = DxcDisassembler(args.dxcompiler) if args.dxcompiler else None
    result["disassembler"] = {"path": str(args.dxcompiler), "sha256": sha256(args.dxcompiler.read_bytes())} if decoder else None
    try:
        for shader in result["shaders"]:
            stem = f'{shader["index"]:04d}_{shader["stage"]}_{shader["offset"]:08x}'
            payload = data[shader["offset"]:shader["offset"] + shader["size"]]
            binary_path = export / f"{stem}.dxbc"
            binary_path.write_bytes(payload)
            shader["export_path"] = str(binary_path.resolve())
            if decoder:
                text = decoder.disassemble(payload)
                text_path = export / f"{stem}.ll"
                text_path.write_text(text, encoding="utf-8")
                shader["disassembly_path"] = str(text_path.resolve())
                shader.update(reflection(text))
        result["all_disassembled"] = bool(decoder)
    finally:
        if decoder:
            decoder.close()
    comparisons = []
    for path in args.compare:
        _, candidate = inspect(path)
        comparisons.append({k: candidate[k] for k in ["source", "sha256", "size", "record_count", "shader_count", "stages", "shader_models"]})
    result["comparisons"] = comparisons
    user_buffers = [cb for cb in result["metadata"]["cbuffers"] if cb["name"] == "UserMaterial"]
    reflection_checks = []
    if decoder and user_buffers:
        expected = {v["name"]: (v["byte_offset"], v["byte_size"]) for v in user_buffers[0]["members"]}
        for shader in result["shaders"]:
            for cb in shader.get("cbuffers", []):
                if cb["name"] == "UserMaterial":
                    actual = {v["name"].removeprefix("VAR_"): (v["offset"], {"float": 4, "float4": 16}.get(v["type"]))
                              for v in cb["variables"]}
                    reflection_checks.append({"shader_index": shader["index"], "matches": actual == expected and cb["size"] == user_buffers[0]["buffer_size"]})
        if any(not item["matches"] for item in reflection_checks):
            raise ValueError("UserMaterial metadata and DXC reflection disagree")
    result["validation"]["user_material_reflection_checks"] = reflection_checks
    (args.out / "inspection.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(args.out / "shaders.csv", [
        {"index": s["index"], "offset": hex(s["offset"]), "size": s["size"], "stage": s["stage"],
         "model": s["shader_model"], "entry": s.get("entry"), "passes": " | ".join(s["pass_names"]), "sha256": s["sha256"]}
        for s in result["shaders"]], ["index", "offset", "size", "stage", "model", "entry", "passes", "sha256"])
    write_csv(args.out / "passes.csv", [
        {"index": r["index"], "offset": hex(r["offset"]), "name": r["name"],
         "shaders": " | ".join(f'{ref["field"]:#x}:{ref["stage"]}#{ref["shader_index"]}' for ref in r["shader_references"])}
        for r in result["records"]], ["index", "offset", "name", "shaders"])
    write_csv(args.out / "cbuffers.csv", [
        {"shader": s["index"], "entry": s.get("entry"), "buffer": cb["name"], "buffer_size": cb["size"],
         "variable": v["name"], "type": v["type"], "byte_offset": v["offset"]}
        for s in result["shaders"] for cb in s.get("cbuffers", []) for v in cb["variables"]],
        ["shader", "entry", "buffer", "buffer_size", "variable", "type", "byte_offset"])
    write_csv(args.out / "metadata_members.csv", [
        {"descriptor_offset": hex(cb["offset"]), "buffer": cb["name"], "buffer_size": cb["buffer_size"],
         "member_record_offset": hex(v["offset"]), "variable": v["name"], "byte_offset": v["byte_offset"], "byte_size": v["byte_size"],
         "name_hash_raw": hex(v["name_hash_raw"])}
        for cb in result["metadata"]["cbuffers"] for v in cb["members"]],
        ["descriptor_offset", "buffer", "buffer_size", "member_record_offset", "variable", "byte_offset", "byte_size", "name_hash_raw"])
    if user_buffers:
        write_csv(args.out / "user_material.csv", [
            {"variable": v["name"], "type": "float4" if v["byte_size"] == 16 else "float",
             "byte_offset": v["byte_offset"], "byte_size": v["byte_size"], "member_record_offset": hex(v["offset"])}
            for v in user_buffers[0]["members"]],
            ["variable", "type", "byte_offset", "byte_size", "member_record_offset"])
    print(json.dumps({k: result[k] for k in ["source", "size", "sha256", "record_count", "shader_count", "stages", "shader_models", "all_disassembled", "comparisons"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
