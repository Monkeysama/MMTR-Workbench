"""Byte-preserving MDF 2.10–2.51 reader and MDF/MMTR binding diagnostics.

The Blender add-on supplied the format reference, but this module deliberately
keeps the editor independent from Blender and does not implement a full
rewriting exporter.  It reads the registered MDF 2.10-2.51 layouts without
normalizing unknown bytes so reports can be tied to the exact source file.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import struct
from typing import Any
import math
import re
from mmtr_editor.hashes import murmur3_x86_32


class MdfError(ValueError):
    pass


# MDF 版本 → (游戏, master 根目录)。只登记 REasy 资源清单确认过版本或本仓库已核对布局的游戏。
MDF_VERSIONS = {
    10: ('RE2', 'mastermaterial/master/'),
    13: ('RE3', 'mastermaterial/master/'),
    19: ('RE8', '_ge/mastermaterial/master/'),
    21: ('RE2RT', 'mastermaterial/master/'),
    23: ('MHRISE', 'mastermaterial/master/'),
    31: ('SF6', 'product/mastermaterial/'),
    32: ('RE4', '_Chainsaw/MasterMaterial/'),
    45: ('MHWilds', 'MaterialShader/'),
    51: ('OnimushaWotS', 'MaterialShader/'),
}

# 材质头、属性头和纹理头的版本差异。字段偏移按 REasy mdf_file.py 的 MatHeader/ParamHeader/TexHeader
# 读取顺序独立核对；v31–45 为 100 字节材质项，v19–30 为 80 字节，v13–18 为 64 字节，
# v7–12 仍为 64 字节但属性头顺序不同。v51（鬼武者）材质项为 104 字节：属性数之后多一个
# `float_count`（属性数据末端 float 数，不含末尾对齐），其后各字段后移 4 字节；另有 108 字节
# 标准头，在 flags 后多 8 字节。两者属性头均为 v31，不能只凭版本号区分。
_LAYOUT_V31 = {
    'material_size': 100, 'property_size': 24, 'texture_size': 32,
    'name': 0, 'name_hash': 8, 'params_size': 12, 'prop_count': 16, 'tex_count': 20,
    'gpbf_name_count': 24, 'gpbf_path_count': 28, 'bake': 32, 'shader_type': 36,
    'flags': 40, 'flags_count': 2, 'shader_lod': 48,
    'prop_headers': 52, 'tex_headers': 60, 'gpbf': 68, 'prop_data': 76,
    'master': 84, 'mmtrs': 92, 'shader_redirects': 96,
    'prop_rel': 16, 'prop_count_in_header': 20, 'prop_flags': 22, 'prop_count_size': 2,
    'prop_layout': 'v31',
}
_LAYOUT_V19 = {
    **_LAYOUT_V31, 'material_size': 80, 'bake': None, 'shader_type': 32,
    'flags': 36, 'flags_count': 1, 'shader_lod': None, 'prop_headers': 40,
    'tex_headers': 48, 'gpbf': 56,
    'prop_data': 64, 'master': 72, 'mmtrs': None, 'shader_redirects': None,
}
_LAYOUT_V13 = {
    **_LAYOUT_V31, 'material_size': 64, 'gpbf_name_count': None, 'gpbf_path_count': None,
    'bake': None, 'shader_type': 24, 'flags': 28, 'flags_count': 1, 'shader_lod': None,
    'prop_headers': 32, 'tex_headers': 40, 'gpbf': None, 'prop_data': 48,
    'master': 56, 'mmtrs': None, 'shader_redirects': None,
}
_LAYOUT_V7 = {
    **_LAYOUT_V13, 'texture_size': 24, 'prop_rel': 20, 'prop_count_in_header': 16,
    'prop_flags': None, 'prop_count_size': 4, 'prop_layout': 'v6',
}
_LAYOUT_V51 = {
    **_LAYOUT_V31, 'material_size': 104, 'float_count': 20, 'shader_redirects': None,
    **{key: value + 4 for key, value in _LAYOUT_V31.items()
       if key not in ('material_size', 'prop_rel', 'prop_count_in_header', 'prop_flags', 'prop_count_size',
                      'prop_layout', 'property_size', 'texture_size', 'shader_redirects')
       and isinstance(value, int) and value > 16},
}
_LAYOUT_V51_STANDARD = {
    **_LAYOUT_V31, 'material_size': 108, 'shader_redirects': None,
    **{key: _LAYOUT_V31[key] + 8 for key in
       ('shader_lod', 'prop_headers', 'tex_headers', 'gpbf', 'prop_data', 'master', 'mmtrs')},
}
MDF_LAYOUTS = {10: _LAYOUT_V7, 13: _LAYOUT_V13, 19: _LAYOUT_V19, 21: _LAYOUT_V19,
               23: _LAYOUT_V19, 31: _LAYOUT_V31, 32: _LAYOUT_V31, 45: _LAYOUT_V31,
               51: _LAYOUT_V51}


def master_root_ok(path: str, version: int | None = None) -> bool:
    """master 是否位于该版本（未给版本时为任一已登记版本）的引擎相对根目录下。"""
    roots = [MDF_VERSIONS[version][1]] if version in MDF_VERSIONS else [root for _game, root in MDF_VERSIONS.values()]
    return any(path.casefold().startswith(root.casefold()) for root in roots)


def mdf_suffix_ok(path: str, version: int | None = None) -> bool:
    """导出路径是否以该版本（未给版本时为任一已登记版本）的 .mdf2.N 结尾。"""
    versions = [version] if version in MDF_VERSIONS else list(MDF_VERSIONS)
    return any(str(path).casefold().endswith(f'.mdf2.{v}') for v in versions)


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _i32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def _string(data: bytes, offset: int) -> str:
    if offset < 16 or offset % 2 or offset >= len(data):
        raise MdfError(f"invalid UTF-16 string offset {offset}")
    end = offset
    while end + 1 < len(data):
        if data[end:end + 2] == b"\0\0":
            try:
                return data[offset:end].decode("utf-16-le")
            except UnicodeDecodeError as exc:
                raise MdfError(f"invalid UTF-16 string at {offset}") from exc
        end += 2
    raise MdfError(f"unterminated UTF-16 string at {offset}")


def _opt_string(data: bytes, offset: int) -> str:
    """空指针表示没有字符串；其余偏移仍交给严格 UTF-16 校验。"""
    return "" if offset == 0 else _string(data, offset)


def _range(data: bytes, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise MdfError(f"{label} exceeds MDF file")
    return data[offset:offset + size]


def _hex_flags(value: int) -> str:
    return f"0x{value & 0xffffffff:08x}"


def user_material_layout(info: dict[str, Any]) -> dict[str, Any]:
    rows = [x for x in info.get('metadata', {}).get('cbuffers', []) if x.get('name') == 'UserMaterial']
    layouts = {(r.get('buffer_size'), tuple((m['name'], m['byte_offset'], m['byte_size']) for m in r.get('members', []))) for r in rows}
    if len(layouts) != 1:
        raise MdfError('UserMaterial 布局缺失或存在多个不同布局，需指定变体后检查。')
    members = rows[0].get('members', [])
    if len({m['name'] for m in members}) != len(members):
        raise MdfError('UserMaterial 包含重复成员名。')
    return rows[0]


def master_matches(master: str, source: str) -> bool:
    expected = master.replace('\\', '/').casefold().strip('/')
    actual = re.sub(r'\.\d+$', '', source.replace('\\', '/').casefold()).strip('/')
    return actual == expected or actual.endswith('/' + expected)


def _public_data(value: Any) -> Any:
    """去除内部指针、原始头和重建标记，供 RPC/JSON 使用。"""
    if isinstance(value, dict):
        return {key: _public_data(item) for key, item in value.items() if not key.startswith('_')}
    if isinstance(value, list):
        return [_public_data(item) for item in value]
    return value


class MdfDocument:
    """Parsed MDF 2.10–2.51 document.

    原始字节用于固定宽度编辑；发生参数或纹理槽增删时，只重建被修改
    材质的尾部表和数据块，并把新块追加到文件末尾。这样可以保留其它
    材质及未知区域，同时让导出结果可重新解析。
    """

    MATERIAL_SIZE = 100
    PROPERTY_SIZE = 24
    TEXTURE_SIZE = 32

    def __init__(self, data: bytes, source: Path | None = None):
        if len(data) < 16 or data[:6] != b"MDF\0\x01\0":
            raise MdfError("expected MDF version 1 header")
        self.original = bytes(data)
        self.source = Path(source).resolve() if source else None
        self.version = int(self.source.suffix[1:]) if self.source and self.source.suffix[1:].isdigit() else 45
        if self.version not in MDF_VERSIONS:
            supported = '、'.join(f'{game} MDF 2.{version}' for version, (game, _root) in sorted(MDF_VERSIONS.items()))
            raise MdfError(f'当前联动适配器只接受已登记的 MDF 版本：{supported}；收到 .{self.version}')
        self.game, self.master_root = MDF_VERSIONS[self.version]
        self.material_count = _u16(data, 6)
        self.layout = MDF_LAYOUTS[self.version]
        if self.version == 51 and self.material_count:
            candidates = []
            for layout in (_LAYOUT_V51, _LAYOUT_V51_STANDARD):
                self.layout = layout
                self.MATERIAL_SIZE = layout['material_size']
                self.PROPERTY_SIZE = layout['property_size']
                self.TEXTURE_SIZE = layout['texture_size']
                try:
                    candidates.append((layout, self._mdf51_materials()))
                except (MdfError, struct.error):
                    continue
            if not candidates:
                raise MdfError('MDF 2.51 材质头不符合已支持布局。')
            if len(candidates) != 1:
                raise MdfError('MDF 2.51 材质头存在布局歧义，拒绝猜测。')
            self.layout, materials = candidates[0]
            if self.layout is _LAYOUT_V51_STANDARD:
                self.game = 'Unknown'
        self.MATERIAL_SIZE = self.layout['material_size']
        self.PROPERTY_SIZE = self.layout['property_size']
        self.TEXTURE_SIZE = self.layout['texture_size']
        if 16 + self.material_count * self.MATERIAL_SIZE > len(data):
            raise MdfError("MDF material table exceeds file")
        self.materials = (materials if self.version == 51 and self.material_count else
                          [self._material(i) for i in range(self.material_count)])
        self.patches: dict[int, bytes] = {}
        self.texture_edits: dict[tuple[int, int], str] = {}
        self.master_edits: dict[int, str] = {}
        self.structural_materials: set[int] = set()
        self.edit_notes: list[dict[str, Any]] = []
        self.saved_sha256 = self.sha256

    @classmethod
    def open(cls, path: str | Path) -> "MdfDocument":
        p = Path(path)
        return cls(p.read_bytes(), p)

    def _mdf51_materials(self) -> list[dict[str, Any]]:
        """验证所有材质项和指针；结构编辑后表可在文件任意尾部位置。"""
        table_end = 16 + self.material_count * self.MATERIAL_SIZE
        if table_end > len(self.original):
            raise MdfError('MDF material table exceeds file')
        fields = ('name', 'prop_headers', 'tex_headers', 'gpbf', 'prop_data', 'master', 'mmtrs')
        for i in range(self.material_count):
            base = 16 + i * self.MATERIAL_SIZE
            for field in fields:
                pointer = _u64(self.original, base + self.layout[field])
                if (field == 'name' and not pointer) or (pointer and not table_end <= pointer <= len(self.original)):
                    raise MdfError(f'material {i} {field} pointer overlaps material table or exceeds MDF')
        return [self._material(i) for i in range(self.material_count)]

    def _material(self, index: int) -> dict[str, Any]:
        data = self.original
        layout = self.layout
        base = 16 + index * self.MATERIAL_SIZE
        name = _string(data, _u64(data, base + layout['name']))
        property_count = _u32(data, base + layout['prop_count'])
        texture_count = _u32(data, base + layout['tex_count'])
        property_headers = _u64(data, base + layout['prop_headers'])
        texture_headers = _u64(data, base + layout['tex_headers'])
        property_data = _u64(data, base + layout['prop_data'])
        property_block_size = _u32(data, base + layout['params_size'])
        _range(data, property_data, 0, f'material {index} property block')
        if property_headers + property_count * self.PROPERTY_SIZE > len(data):
            raise MdfError(f"material {index} property table exceeds MDF")
        if texture_headers + texture_count * self.TEXTURE_SIZE > len(data):
            raise MdfError(f"material {index} texture table exceeds MDF")
        properties = []
        for j in range(property_count):
            p = property_headers + j * self.PROPERTY_SIZE
            prop_name = _string(data, _u64(data, p))
            value_offset = _i32(data, p + layout['prop_rel'])
            count = (_u16(data, p + layout['prop_count_in_header']) if layout['prop_count_size'] == 2
                     else _u32(data, p + layout['prop_count_in_header']))
            prop_flags = (_u16(data, p + layout['prop_flags']) if layout['prop_flags'] is not None else 0)
            if not count or value_offset < 0 or value_offset + count * 4 > property_block_size:
                raise MdfError(f'property {prop_name} exceeds material property block')
            raw = _range(data, property_data + value_offset, count * 4, f"property {prop_name}")
            properties.append({
                "name": prop_name, "offset": value_offset, "count": count, "byte_size": len(raw),
                "flags": prop_flags, "raw_hex": raw.hex(),
                "values": list(struct.unpack("<" + "f" * count, raw)),
                "_absolute_offset": property_data + value_offset,
                "_name_pointer": _u64(data, p), "_unknown_a": _u32(data, p + 8), "_unknown_b": _u32(data, p + 12),
            })
        if property_data + property_block_size > len(data):
            used = max((p['offset'] + p['byte_size'] for p in properties), default=0)
            aligned = (used + 15) // 16 * 16
            if index != self.material_count - 1 or property_block_size != aligned or len(data) != property_data + used:
                raise MdfError(f'material {index} property block exceeds MDF file')
        textures = []
        for j in range(texture_count):
            t = texture_headers + j * self.TEXTURE_SIZE
            tex_name = _opt_string(data, _u64(data, t))
            tex_path = _opt_string(data, _u64(data, t + 16))
            textures.append({"name": tex_name, "path": tex_path, "_raw_header": bytes(data[t:t + self.TEXTURE_SIZE]),
                             "_name_pointer": _u64(data, t), "_path_pointer": _u64(data, t + 16)})
        mmtrs_offset = _u64(data, base + layout['mmtrs']) if layout['mmtrs'] is not None else 0
        mmtrs = []
        if mmtrs_offset:
            _range(data, mmtrs_offset, 64, 'MMTRS table')
            for j in range(8):
                ptr = _u64(data, mmtrs_offset + j * 8)
                _range(data, ptr, 4, 'MMTRS count')
                count = _u32(data, ptr)
                raw = _range(data, ptr + 4, count * 4, 'MMTRS indices')
                mmtrs.append(list(struct.unpack('<' + 'I' * count, raw)))
        gpbf = []
        gpbf_count = _u32(data, base + layout['gpbf_name_count']) if layout['gpbf_name_count'] is not None else 0
        gpbf_offset = _u64(data, base + layout['gpbf']) if layout['gpbf'] is not None else 0
        if gpbf_count:
            _range(data, gpbf_offset, gpbf_count * 32, 'GPBF table')
            for j in range(gpbf_count):
                entry = gpbf_offset + j * 32
                gpbf.append({'name': _opt_string(data, _u64(data, entry)), 'path': _opt_string(data, _u64(data, entry + 16))})
        flags = [_u32(data, base + layout['flags'] + lane * 4) for lane in range(layout['flags_count'])]
        flags += [0] * (2 - len(flags))
        return {
            "index": index, "offset": base, "name": name,
            "master": _opt_string(data, _u64(data, base + layout['master'])),
            "shader_type": _i32(data, base + layout['shader_type']),
            "name_hash": _hex_flags(_u32(data, base + layout['name_hash'])),
            "shader_lod": _i32(data, base + layout['shader_lod']) if layout['shader_lod'] is not None else 0,
            "bake_texture_array_size": _i32(data, base + layout['bake']) if layout['bake'] is not None else 0,
            "gpbf_name_count": gpbf_count,
            "gpbf_path_count": _u32(data, base + layout['gpbf_path_count']) if layout['gpbf_path_count'] is not None else 0,
            "gpbf": gpbf, "mmtrs": mmtrs, "flags": flags,
            "flags_hex": [_hex_flags(value) for value in flags],
            "property_count": property_count, "texture_count": texture_count,
            "property_block_size": property_block_size,
            **({"property_float_count": _u32(data, base + layout['float_count'])}
               if layout.get('float_count') is not None else {}),
            "properties": properties, "textures": textures,
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.original).hexdigest()

    def material(self, index: int) -> dict[str, Any]:
        if not isinstance(index, int) or not 0 <= index < len(self.materials):
            raise MdfError("invalid MDF material index")
        return self.materials[index]

    def snapshot(self, index: int | None = None) -> dict[str, Any]:
        selected = _public_data(self.material(index)) if index is not None else None
        return {
            "source": str(self.source) if self.source else "",
            "size": len(self.original),
            "sha256": self.sha256,
            "version": self.version,
            "game": self.game,
            "material_count": len(self.materials),
            "materials": [
                {k: m[k] for k in ("index", "name", "master", "shader_type", "flags", "flags_hex", "property_count", "texture_count")}
                for m in self.materials
            ],
            "selected": selected,
            "dirty": bool(self.patches or self.texture_edits or self.master_edits or self.structural_materials),
            "changes": list(self.edit_notes),
            "unsaved": hashlib.sha256(self._patched_bytes()).hexdigest() != self.saved_sha256,
        }

    def set_property(self, index: int, name: str, values: list[float]) -> None:
        material = self.material(index)
        prop = next((p for p in material["properties"] if p["name"] == name), None)
        if prop is None:
            raise MdfError(f"MDF 属性不存在：{name}")
        if len(values) != prop["count"]:
            raise MdfError(f"{name} 需要 {prop['count']} 个 float 值")
        if not all(math.isfinite(float(x)) for x in values):
            raise MdfError("MDF 属性不能包含 NaN 或无穷值")
        try:
            raw = struct.pack("<" + "f" * len(values), *(float(x) for x in values))
        except (OverflowError, struct.error) as exc:
            raise MdfError('属性值超出 float32 范围。') from exc
        if index in self.structural_materials:
            prop['raw_hex'] = raw.hex()
            prop['values'] = list(struct.unpack('<' + 'f' * len(values), raw))
            self.edit_notes = [x for x in self.edit_notes if not (x['material_index'] == index and x['kind'] == 'property' and x['name'] == name)]
            self.edit_notes.append({'kind': 'property', 'material_index': index, 'name': name, 'values': prop['values']})
            return
        offset = prop['_absolute_offset']
        for m in self.materials:
            for p in m['properties']:
                if p is prop:
                    continue
                if offset < p['_absolute_offset'] + p['byte_size'] and p['_absolute_offset'] < offset + len(raw):
                    raise MdfError('属性数据被其他属性或材质共享，当前禁止原位修改。')
        if self.original[offset:offset + len(raw)] == raw:
            self.patches.pop(offset, None)
        else:
            self.patches[offset] = raw
        prop["raw_hex"] = raw.hex()
        prop["values"] = list(struct.unpack("<" + "f" * len(values), raw))
        self.edit_notes = [x for x in self.edit_notes if not (x["material_index"] == index and x["kind"] == "property" and x["name"] == name)]
        if offset in self.patches:
            self.edit_notes.append({"kind": "property", "material_index": index, "name": name, "values": prop["values"]})

    def set_property_components(self, index: int, name: str, components: dict[str, float]) -> None:
        prop = next((p for p in self.material(index)['properties'] if p['name'] == name), None)
        if prop is None:
            raise MdfError(f'MDF 属性不存在：{name}')
        if not isinstance(components, dict) or not components:
            raise MdfError('请提供要修改的参数分量。')
        values = prop['values'][:]
        for key, value in components.items():
            if not isinstance(key, str) or not key.isascii() or not key.isdigit() or str(int(key)) != key or not 0 <= int(key) < len(values):
                raise MdfError('参数分量索引无效。')
            if type(value) not in (int, float):
                raise MdfError('参数分量必须是数字。')
            values[int(key)] = value
        self.set_property(index, name, values)

    def set_flags(self, index: int, flags_a: int | None = None, flags_b: int | None = None) -> None:
        material = self.material(index)
        if any(value is not None and (type(value) is not int or not 0 <= value <= 0xffffffff) for value in (flags_a, flags_b)):
            raise MdfError('flags 必须是 32 位无符号整数')
        if flags_b is not None and self.layout['flags_count'] < 2:
            raise MdfError('该 MDF 版本没有第二个 flags 字段。')
        for lane, value in ((0, flags_a), (1, flags_b)):
            if value is None:
                continue
            offset = material["offset"] + self.layout['flags'] + lane * 4
            self._patch(offset, struct.pack("<I", value))
            material["flags"][lane] = value
            material["flags_hex"][lane] = _hex_flags(value)
        self.edit_notes = [x for x in self.edit_notes if not (x["material_index"] == index and x["kind"] == "flags")]
        if any(material['offset'] + self.layout['flags'] + lane * 4 in self.patches
               for lane in range(self.layout['flags_count'])):
            self.edit_notes.append({"kind": "flags", "material_index": index, "flags": material["flags_hex"][:]})

    def _patch(self, offset: int, raw: bytes) -> None:
        if self.original[offset:offset + len(raw)] == raw:
            self.patches.pop(offset, None)
        else:
            self.patches[offset] = raw

    def set_setting(self, index: int, field: str, value: int) -> None:
        offsets = {'shader_type': self.layout['shader_type'], 'shader_lod': self.layout['shader_lod'],
                   'bake_texture_array_size': self.layout['bake']}
        if field not in offsets or offsets[field] is None or type(value) is not int or not -0x80000000 <= value <= 0x7fffffff:
            raise MdfError('设置项需要该 MDF 版本存在的 int32 字段和值。')
        material = self.material(index)
        offset = material['offset'] + offsets[field]
        self._patch(offset, struct.pack('<i', value))
        material[field] = value
        self.edit_notes = [x for x in self.edit_notes if not (x['material_index'] == index and x['kind'] == field)]
        if offset in self.patches:
            self.edit_notes.append({'kind': field, 'material_index': index, 'value': value})

    # 仅更改所选材质的master指针；原共享字符串不写入，不隐式调整Shader或其它材质。
    def set_master(self, index: int, path: str) -> None:
        material = self.material(index)
        if not isinstance(path, str) or not path.strip():
            raise MdfError('master 路径不能为空。')
        path = path.strip().replace('\\', '/')
        if (any(ord(c) < 32 for c in path) or ':' in path
                or any(part in ('', '.', '..') for part in path.split('/'))
                or not master_root_ok(path, self.version)
                or not path.casefold().endswith('.mmtr')):
            raise MdfError(f'master 必须位于 {self.master_root} 下、以 .mmtr 结尾，且不得包含版本后缀或目录跳转。')
        try:
            encoded = path.encode('utf-16-le')
        except UnicodeError as exc:
            raise MdfError('master 路径不是有效的 UTF-16 文本。') from exc
        if len(encoded) > 65534:
            raise MdfError('master 路径过长')
        original = _opt_string(self.original, _u64(self.original, material['offset'] + self.layout['master']))
        self.edit_notes = [note for note in self.edit_notes
                           if not (note['material_index'] == index and note['kind'] == 'master')]
        if path == original:
            self.master_edits.pop(index, None)
        else:
            self.master_edits[index] = path
            self.edit_notes.append({'kind': 'master', 'material_index': index, 'old': original, 'path': path})
        material['master'] = path

    def set_texture(self, index: int, slot: int, path: str) -> None:
        material = self.material(index)
        if type(slot) is not int or not 0 <= slot < len(material['textures']):
            raise MdfError('纹理槽位无效。')
        if not isinstance(path, str) or not path.strip() or '\0' in path:
            raise MdfError('纹理路径不能为空或含 NUL。')
        try:
            encoded = path.encode('utf-16-le')
        except UnicodeError as exc:
            raise MdfError('纹理路径不是有效的 UTF-16 文本。') from exc
        if len(encoded) > 65534:
            raise MdfError('纹理路径过长')
        if index in self.structural_materials:
            key = (index, slot)
            material['textures'][slot]['path'] = path
            self.texture_edits[key] = path
            self.edit_notes = [x for x in self.edit_notes if not (x['material_index'] == index and x['kind'] == 'texture' and x['slot'] == slot)]
            self.edit_notes.append({'kind': 'texture', 'material_index': index, 'slot': slot, 'path': path})
            return
        header = _u64(self.original, material['offset'] + self.layout['tex_headers']) + slot * self.TEXTURE_SIZE
        for other in self.materials:
            start = _u64(self.original, other['offset'] + self.layout['tex_headers'])
            for other_slot in range(other['texture_count']):
                if other['index'] == index and other_slot == slot:
                    continue
                other_header = start + other_slot * self.TEXTURE_SIZE
                if header + 16 < other_header + self.TEXTURE_SIZE and other_header < header + 24:
                    raise MdfError('纹理头与其它纹理重叠，停止原位修改。')
        original_path = _opt_string(self.original, _u64(self.original, header + 16))
        key = (index, slot)
        if path == original_path:
            self.texture_edits.pop(key, None)
        else:
            self.texture_edits[key] = path
        material['textures'][slot]['path'] = path
        self.edit_notes = [x for x in self.edit_notes if not (x['material_index'] == index and x['kind'] == 'texture' and x['slot'] == slot)]
        if key in self.texture_edits:
            self.edit_notes.append({'kind': 'texture', 'material_index': index, 'slot': slot, 'path': path})

    def add_property(self, index: int, name: str, values: list[float], flags: int = 0, offset: int | None = None) -> None:
        """新增 MDF 参数；调用方负责在 MMTR 已加载时检查 Shader 接口。"""
        material = self.material(index)
        if not isinstance(name, str) or not name.strip() or '\0' in name:
            raise MdfError('参数名称不能为空或含 NUL。')
        try:
            name.encode('utf-16-le')
        except UnicodeError as exc:
            raise MdfError('参数名称不是有效的 UTF-16 文本。') from exc
        if any(p['name'] == name for p in material['properties']):
            raise MdfError(f'MDF 参数已存在：{name}')
        if not isinstance(values, list) or not 1 <= len(values) <= 0xffff:
            raise MdfError('参数必须包含 1 到 65535 个 float 值。')
        if not all(type(x) in (int, float) and math.isfinite(float(x)) for x in values):
            raise MdfError('MDF 属性不能包含 NaN 或无穷值。')
        if type(flags) is not int or not 0 <= flags <= 0xffff:
            raise MdfError('参数 flags 必须是 16 位无符号整数。')
        if flags and self.layout['prop_flags'] is None:
            raise MdfError('当前 MDF 版本的属性头没有 flags 字段。')
        try:
            raw = struct.pack('<' + 'f' * len(values), *(float(x) for x in values))
        except (OverflowError, struct.error) as exc:
            raise MdfError('属性值超出 float32 范围。') from exc
        if offset is not None and (type(offset) is not int or offset < 0 or offset % 4):
            raise MdfError('参数 offset 必须是非负的 4 字节对齐偏移。')
        if offset is not None:
            for prop in material['properties']:
                if offset < prop['offset'] + prop['byte_size'] and prop['offset'] < offset + len(raw):
                    raise MdfError('新增参数的 MMTR 偏移与现有 MDF 参数重叠。')
        material['properties'].append({
            'name': name, 'offset': 0, 'count': len(values), 'byte_size': len(raw),
            'flags': flags, 'raw_hex': raw.hex(), 'values': list(struct.unpack('<' + 'f' * len(values), raw)),
            '_absolute_offset': 0,
            '_unknown_a': murmur3_x86_32(name.encode('utf-16-le'), 0xffffffff),
            '_unknown_b': murmur3_x86_32(name.encode('utf-8'), 0xffffffff), '_new': True,
            '_requested_offset': offset,
        })
        material['property_count'] = len(material['properties'])
        self.structural_materials.add(index)
        self.edit_notes = [x for x in self.edit_notes if not (x['material_index'] == index and x['kind'] == 'add_property' and x['name'] == name)]
        self.edit_notes.append({'kind': 'add_property', 'material_index': index, 'name': name, 'count': len(values)})

    def delete_property(self, index: int, name: str) -> None:
        """删除 MDF 参数并重建该材质的参数表。"""
        material = self.material(index)
        position = next((i for i, p in enumerate(material['properties']) if p['name'] == name), None)
        if position is None:
            raise MdfError(f'MDF 属性不存在：{name}')
        material['properties'].pop(position)
        material['property_count'] = len(material['properties'])
        self.structural_materials.add(index)
        self.edit_notes = [x for x in self.edit_notes if not (x['material_index'] == index and x['kind'] == 'delete_property' and x['name'] == name)]
        self.edit_notes.append({'kind': 'delete_property', 'material_index': index, 'name': name})

    def add_texture(self, index: int, name: str, path: str) -> None:
        """新增 MDF 纹理槽；槽名必须唯一，路径写入新纹理表。"""
        material = self.material(index)
        if not isinstance(name, str) or not name.strip() or '\0' in name:
            raise MdfError('纹理槽名称不能为空或含 NUL。')
        try:
            name.encode('utf-16-le')
        except UnicodeError as exc:
            raise MdfError('纹理槽名称不是有效的 UTF-16 文本。') from exc
        if any(t['name'] == name for t in material['textures']):
            raise MdfError(f'MDF 纹理槽已存在：{name}')
        if not isinstance(path, str) or not path.strip() or '\0' in path:
            raise MdfError('纹理路径不能为空或含 NUL。')
        try:
            encoded_path = path.encode('utf-16-le')
        except UnicodeError as exc:
            raise MdfError('纹理路径不是有效的 UTF-16 文本。') from exc
        if len(encoded_path) > 65534:
            raise MdfError('纹理路径过长。')
        header = bytearray(self.TEXTURE_SIZE)
        struct.pack_into('<II', header, 8,
                         murmur3_x86_32(name.encode('utf-16-le'), 0xffffffff),
                         murmur3_x86_32(name.encode('utf-8'), 0xffffffff))
        material['textures'].append({'name': name, 'path': path, '_raw_header': bytes(header), '_new': True})
        material['texture_count'] = len(material['textures'])
        self.structural_materials.add(index)
        slot = len(material['textures']) - 1
        self.edit_notes.append({'kind': 'add_texture', 'material_index': index, 'slot': slot, 'name': name, 'path': path})

    def delete_texture(self, index: int, slot: int) -> None:
        """删除 MDF 纹理槽并重建该材质的纹理表。"""
        material = self.material(index)
        if type(slot) is not int or not 0 <= slot < len(material['textures']):
            raise MdfError('纹理槽索引无效。')
        removed = material['textures'].pop(slot)
        material['texture_count'] = len(material['textures'])
        self.structural_materials.add(index)
        self.edit_notes.append({'kind': 'delete_texture', 'material_index': index, 'slot': slot, 'name': removed['name']})

    def reset(self) -> None:
        self.patches.clear()
        self.texture_edits.clear()
        self.master_edits.clear()
        self.structural_materials.clear()
        self.edit_notes.clear()
        fresh = MdfDocument(self.original, self.source)
        self.materials = fresh.materials

    def _patched_bytes(self) -> bytes:
        result = bytearray(self.original)
        for offset, raw in self.patches.items():
            result[offset:offset + len(raw)] = raw
        if self.structural_materials:
            result = self._rebuild_structural_tables(result)
        if self.texture_edits or self.master_edits:
            declared_end = max((_u64(self.original, m['offset'] + self.layout['prop_data']) + m['property_block_size']
                                for m in self.materials), default=len(result))
            result.extend(b'\0' * max(0, declared_end - len(result)))
            result.extend(b'\0' * (len(result) % 2))
            for (index, slot), path in sorted(self.texture_edits.items()):
                if index in self.structural_materials:
                    continue
                material = self.material(index)
                header = _u64(self.original, material['offset'] + self.layout['tex_headers']) + slot * self.TEXTURE_SIZE
                struct.pack_into('<Q', result, header + 16, len(result))
                result.extend(path.encode('utf-16-le') + b'\0\0')
            for index, path in sorted(self.master_edits.items()):
                struct.pack_into('<Q', result, self.material(index)['offset'] + self.layout['master'], len(result))
                result.extend(path.encode('utf-16-le') + b'\0\0')
        return bytes(result)

    def _rebuild_structural_tables(self, result: bytearray) -> bytearray:
        """追加被修改材质的属性/纹理数据块，并更新该材质的头指针。"""
        string_ptrs: dict[str, int] = {}
        for material in self.materials:
            for relative in (0, self.layout['master']):
                pointer = _u64(self.original, material['offset'] + relative)
                if pointer:
                    string_ptrs.setdefault(_string(self.original, pointer), pointer)
            for prop in material['properties']:
                if not prop.get('_new'):
                    string_ptrs.setdefault(prop['name'], prop['_name_pointer'])
            for tex in material['textures']:
                if tex.get('_new'):
                    continue
                if tex['_name_pointer']:
                    string_ptrs.setdefault(_opt_string(self.original, tex['_name_pointer']), tex['_name_pointer'])
                if tex['_path_pointer']:
                    string_ptrs.setdefault(_opt_string(self.original, tex['_path_pointer']), tex['_path_pointer'])
        def align(value: int, boundary: int) -> int:
            return (value + boundary - 1) // boundary * boundary
        cursor = align(len(result), 8)
        if cursor > len(result):
            result.extend(b'\0' * (cursor - len(result)))
        def string_pointer(value: str) -> int:
            nonlocal cursor
            if not value:
                return 0
            if value in string_ptrs:
                return string_ptrs[value]
            cursor = align(cursor, 2)
            pointer = cursor
            encoded = value.encode('utf-16-le') + b'\0\0'
            result.extend(encoded)
            cursor += len(encoded)
            string_ptrs[value] = pointer
            return pointer
        for index in sorted(self.structural_materials):
            material = self.material(index)
            for prop in material['properties']:
                string_pointer(prop['name'])
            for texture in material['textures']:
                string_pointer(texture['name'])
                string_pointer(texture['path'])
        for index in sorted(self.structural_materials):
            material = self.material(index)
            end = 0
            for prop in material['properties']:
                if not prop.get('_new'):
                    end = max(end, prop['offset'] + prop['byte_size'])
            for prop in material['properties']:
                if prop.get('_new'):
                    desired = prop.get('_requested_offset') if prop.get('_requested_offset') is not None else align(end, 4)
                    occupied = [other for other in material['properties'] if other is not prop and not other.get('_new')]
                    occupied += [other for other in material['properties'] if other is not prop and other.get('_new') and other.get('_absolute_offset', 0)]
                    if any(desired < other['offset'] + other['byte_size'] and other['offset'] < desired + prop['byte_size'] for other in occupied):
                        raise MdfError('新增属性偏移与现有属性数据范围重叠。')
                    prop['offset'] = desired
                    end = prop['offset'] + prop['byte_size']
            block_size = align(end, 16)
            block = bytearray(block_size)
            old_data = _u64(self.original, material['offset'] + self.layout['prop_data'])
            old_size = material.get('property_block_size', 0)
            if old_data < len(self.original):
                block[:min(block_size, max(0, len(self.original) - old_data), old_size)] = self.original[old_data:old_data + min(block_size, max(0, len(self.original) - old_data), old_size)]
            for prop in material['properties']:
                raw = struct.pack('<' + 'f' * prop['count'], *(float(x) for x in prop['values']))
                block[prop['offset']:prop['offset'] + len(raw)] = raw
            cursor = align(cursor, 16)
            property_data = cursor
            result.extend(b'\0' * (cursor - len(result)))
            result.extend(block)
            cursor += len(block)
            cursor = align(cursor, 8)
            property_headers = cursor
            result.extend(b'\0' * (cursor - len(result)))
            for prop in material['properties']:
                name_ptr = string_pointer(prop['name'])
                if self.layout['prop_layout'] == 'v31':
                    packed = struct.pack('<QIIIHH', name_ptr, prop.get('_unknown_a', 0), prop.get('_unknown_b', 0),
                                         prop['offset'], prop['count'], prop['flags'])
                else:
                    packed = struct.pack('<QIIII', name_ptr, prop.get('_unknown_a', 0), prop.get('_unknown_b', 0),
                                         prop['count'], prop['offset'])
                result.extend(packed)
                prop['_absolute_offset'] = property_data + prop['offset']
            cursor = len(result)
            cursor = align(cursor, 8)
            texture_headers = cursor
            result.extend(b'\0' * (cursor - len(result)))
            for texture in material['textures']:
                raw_header = texture.get('_raw_header', b'\0' * self.TEXTURE_SIZE)
                header = bytearray(raw_header[:self.TEXTURE_SIZE].ljust(self.TEXTURE_SIZE, b'\0'))
                struct.pack_into('<Q', header, 0, string_pointer(texture['name']))
                struct.pack_into('<Q', header, 16, string_pointer(texture['path']))
                result.extend(header)
                texture['_raw_header'] = bytes(header)
            cursor = len(result)
            base = material['offset']
            struct.pack_into('<I', result, base + self.layout['params_size'], block_size)
            struct.pack_into('<I', result, base + self.layout['prop_count'], len(material['properties']))
            struct.pack_into('<I', result, base + self.layout['tex_count'], len(material['textures']))
            struct.pack_into('<Q', result, base + self.layout['prop_headers'], property_headers)
            struct.pack_into('<Q', result, base + self.layout['tex_headers'], texture_headers)
            struct.pack_into('<Q', result, base + self.layout['prop_data'], property_data)
            material['property_block_size'] = block_size
            material['property_count'] = len(material['properties'])
            material['texture_count'] = len(material['textures'])
            if self.layout.get('float_count') is not None:
                # 鬼武者的 float_count 记录属性数据实际末端，不含 block_size 的末尾对齐。
                material['property_float_count'] = end // 4
                struct.pack_into('<I', result, base + self.layout['float_count'], end // 4)
        return result

    def build(self) -> tuple[bytes, dict[str, Any]]:
        result = self._patched_bytes()
        reparsed = MdfDocument(result, self.source)
        if _public_data(reparsed.materials) != _public_data(self.materials):
            raise MdfError('MDF 重新解析结果与预期材料不符，拒绝导出。')
        ranges = ([{"offset": offset, "size": len(raw)} for offset, raw in self.patches.items()] +
                  [{"offset": self.material(i)['offset'] + self.layout['master'], "size": 8} for i in self.master_edits] +
                  [{"offset": _u64(self.original, self.material(i)['offset'] + self.layout['tex_headers']) + slot * self.TEXTURE_SIZE + 16,
                    "size": 8} for i, slot in self.texture_edits if i not in self.structural_materials])
        for index in sorted(self.structural_materials):
            ranges.append({"offset": self.material(index)['offset'], "size": self.MATERIAL_SIZE})
        if len(result) > len(self.original):
            ranges.append({"offset": len(self.original), "size": len(result) - len(self.original)})
        return bytes(result), {
            "source": str(self.source) if self.source else "",
            "source_sha256": self.sha256,
            "sha256": hashlib.sha256(result).hexdigest(),
            "size": len(result),
            "byte_ranges": sorted(ranges, key=lambda x: x['offset']),
            "changes": list(self.edit_notes),
            "byte_length_unchanged": len(result) == len(self.original),
            "unknown_bytes_preserved": True,
            "reparse_valid": True,
        }

    def binding_report(self, index: int, mmtr_info: dict[str, Any] | None) -> dict[str, Any]:
        material = self.material(index)
        report: dict[str, Any] = {
            "material_index": index,
            "material_name": material["name"],
            "master": material["master"],
            "shared_material_count": sum(m["master"] == material["master"] for m in self.materials),
            "mmtr": None,
            "properties": [],
            "extra_mdf_properties": [],
            "textures": [],
            "extra_mdf_textures": [],
            "flags": {"shader_type": material["shader_type"], "a": material["flags_hex"][0], "b": material["flags_hex"][1]},
        }
        if not mmtr_info:
            report["status"] = "mmtr_not_loaded"
            return report
        layout = user_material_layout(mmtr_info)
        report['comparison_mode'] = 'master' if master_matches(material['master'], mmtr_info.get('source', '')) else 'candidate'
        report['scope'] = 'metadata_layout_only'
        report["mmtr"] = {
            "source": mmtr_info.get("source", ""),
            "sha256": mmtr_info.get("sha256", ""),
            "user_material_size": layout.get('buffer_size'),
        }
        members = layout['members']
        mdf_props = {p["name"]: p for p in material["properties"]}
        seen = set()
        for member in members:
            name = member["name"]
            prop = mdf_props.get(name)
            seen.add(name)
            if prop is None:
                status = "missing_in_mdf"
            elif prop["offset"] != member["byte_offset"] or prop["byte_size"] != member["byte_size"]:
                status = "offset_or_width_mismatch"
            else:
                status = "match"
            report["properties"].append({
                "name": name,
                "mmtr_offset": member["byte_offset"],
                "mmtr_size": member["byte_size"],
                "mdf_offset": prop["offset"] if prop else None,
                "mdf_size": prop["byte_size"] if prop else None,
                "status": status,
            })
        report["extra_mdf_properties"] = [
            {"name": p["name"], "offset": p["offset"], "byte_size": p["byte_size"]}
            for p in material["properties"] if p["name"] not in seen
        ]
        report["missing_mdf_properties"] = [
            {"name": row["name"], "mmtr_offset": row["mmtr_offset"], "mmtr_size": row["mmtr_size"]}
            for row in report["properties"] if row["status"] == "missing_in_mdf"
        ]
        srv_names = {x.get("name") for x in mmtr_info.get("metadata", {}).get("srvs", []) if x.get("name")}
        mdf_texture_names = {x["name"] for x in material["textures"]}
        for texture in material["textures"]:
            report["textures"].append({"name": texture["name"], "path": texture["path"], "status": "name_in_mmtr_metadata" if texture["name"] in srv_names else "not_in_mmtr_metadata"})
        report["extra_mdf_textures"] = [x for x in report["textures"] if x["status"] == "not_in_mmtr_metadata"]
        report["missing_mdf_textures"] = [{"name": name} for name in sorted(srv_names - mdf_texture_names)]
        differences = [x for x in report['properties'] if x['status'] != 'match']
        non_reserve = [x for x in differences if not (x['status'] == 'missing_in_mdf' and re.fullmatch(r'CAPCOM_MATERIAL_RESERVE\d*', x['name']))]
        review = non_reserve or report['extra_mdf_properties'] or report['extra_mdf_textures']
        report['status'] = 'review_required' if review else ('compatible_with_reserves' if differences else 'ok')
        return report
