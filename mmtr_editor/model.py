"""Lossless MMTR editing. No game process or original resource is modified."""
from __future__ import annotations

import hashlib
import copy
import json
import os
from pathlib import Path
import re
import struct
import tempfile

from inspect_mmtr import FORMATS, TABLE_FIELDS, decode_dxbc, inspect_data
from mmtr_editor.hashes import murmur3_x86_32, xxh64

SHADER_FIELDS = (8, 16, 24, 32, 40, 48, 56)


class EditError(ValueError):
    pass


def atomic_write(path: Path, data: bytes):
    """Publish a complete file, retaining an existing destination on write failure."""
    path = Path(path)
    if not path.parent.is_dir():
        raise EditError('输出文件夹不存在。')
    fd, temporary = tempfile.mkstemp(prefix='.mmtr-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_shader(data: bytes):
    try:
        info = decode_dxbc(data, 0)
        if info['size'] != len(data):
            raise EditError('输入必须是一个完整的 DXBC；不能带 MMTR 尾项或多余字节。')
        tags = [c['tag'] for c in info['chunks']]
        if len(tags) != len(set(tags)):
            raise EditError('DXBC 含有重复 chunk。')
        return info
    except (ValueError, struct.error, UnicodeError) as exc:
        raise EditError(f'DXBC 结构无效：{exc}') from exc


def interface_chunks(data: bytes):
    info = validate_shader(data)
    return {c['tag']: data[c['offset'] + 8:c['offset'] + 8 + c['size']]
            for c in info['chunks'] if c['tag'] in ('ISG1', 'OSG1', 'PSG1', 'SFI0')}


def _shift_pointer(value: int, insertion: int, delta: int) -> int:
    """把 MMTR 前缀插入后的绝对指针映射到新文件。"""
    return value + delta if value >= insertion else value


def _metadata_pointer_locations(info: dict) -> set[int]:
    """收集记录、metadata 描述符、成员条目中的所有已知 u64 指针位置。"""
    locations: set[int] = set()
    for record in info['records']:
        locations.add(record['offset'])
        locations.update(record['offset'] + field for field in SHADER_FIELDS)
        for field, _count, _width in TABLE_FIELDS.values():
            locations.add(record['offset'] + field)
    for entries in info['metadata'].values():
        for entry in entries:
            locations.add(entry['offset'])
            if entry.get('name_offset') is not None:
                locations.add(entry['offset'])
            if entry.get('name') == 'UserMaterial' or 'members_offset' in entry:
                locations.add(entry['offset'] + 24)
            for member in entry.get('members', []):
                locations.add(member['offset'])
    return locations


def _add_cbuffer_member_bytes(data: bytes, cbuffer_name: str, member_name: str,
                              byte_size: int, byte_offset: int, source: Path) -> bytes:
    """在所有同名 cbuffer 描述符共享的成员表末尾插入一个参数。"""
    _, info = inspect_data(data, source)
    entries = [entry for entry in info['metadata']['cbuffers'] if entry['name'] == cbuffer_name]
    if not entries:
        raise EditError(f'找不到常量缓冲：{cbuffer_name}')
    if not member_name or any(not c.isascii() or c in '\x00\r\n' for c in member_name):
        raise EditError('MMTR 参数名称必须是非空 ASCII。')
    if type(byte_size) is not int or byte_size <= 0 or byte_size > 0xffff:
        raise EditError('MMTR 参数字节宽度无效。')
    if type(byte_offset) is not int or byte_offset < 0 or byte_offset > 0xffff or byte_offset % 4:
        raise EditError('MMTR 参数偏移必须是非负的 4 字节对齐值。')
    first = entries[0]
    if any(entry['members_offset'] != first['members_offset'] or entry['member_count'] != first['member_count']
           for entry in entries):
        raise EditError(f'{cbuffer_name} 的成员表不一致，拒绝批量新增。')
    if any(member['name'] == member_name for member in first['members']):
        raise EditError(f'MMTR 参数已存在：{member_name}')
    if byte_offset < first['buffer_size']:
        raise EditError(f'新增 MMTR 参数偏移必须不小于当前常量缓冲末尾 {first["buffer_size"]} 字节（当前为 {byte_offset}）。')
    if byte_offset + byte_size > 0xffff:
        raise EditError('MMTR 参数超出 16 位布局范围。')
    new_size = (byte_offset + byte_size + 15) & ~15
    if new_size > 0xffff:
        raise EditError('MMTR 常量缓冲大小超出 16 位范围。')

    old_start = info['shader_start']
    name_bytes = member_name.encode('ascii') + b'\0'
    # 尾部复制成员表；绑定/状态/未知块不移动，避免遗漏其内部或根指针。
    prefix = bytearray(data[:old_start])
    new_name_offset = len(prefix)
    prefix.extend(name_bytes)
    member_offset = len(prefix)
    prefix.extend(data[first['members_offset']:first['members_offset']+first['member_count']*16])
    prefix.extend(struct.pack('<QIHH', new_name_offset, murmur3_x86_32(member_name.encode('ascii'), 0xffffffff), byte_offset, byte_size))
    new_shader_start = len(prefix)
    for record in info['records']:
        for ref in record['shader_references']:
            struct.pack_into('<Q', prefix, record['offset']+ref['field'], ref['offset']+new_shader_start-old_start)
    for entry in entries:
        descriptor = entry['offset']
        struct.pack_into('<I', prefix, descriptor + 16, new_size)
        struct.pack_into('<I', prefix, descriptor + 20, entry['member_count'] + 1)
        struct.pack_into('<Q', prefix, descriptor + 24, member_offset)
    struct.pack_into('<Q', prefix, info['record_start'] - 8, new_shader_start)
    return bytes(prefix) + data[old_start:]


def _remove_cbuffer_member_bytes(data: bytes, cbuffer_name: str, member_name: str, source: Path) -> bytes:
    """移除同名 cbuffer 的成员并压缩共享成员表，不改变文件前缀长度。"""
    _, info = inspect_data(data, source)
    entries = [entry for entry in info['metadata']['cbuffers'] if entry['name'] == cbuffer_name]
    if not entries:
        raise EditError(f'找不到常量缓冲：{cbuffer_name}')
    first = entries[0]
    if any(entry['members_offset'] != first['members_offset'] or entry['member_count'] != first['member_count']
           for entry in entries):
        raise EditError(f'{cbuffer_name} 的成员表不一致，拒绝批量删除。')
    index = next((i for i, member in enumerate(first['members']) if member['name'] == member_name), None)
    if index is None:
        raise EditError(f'MMTR 参数不存在：{member_name}')
    result = bytearray(data)
    members = first['members']
    for i in range(index, len(members) - 1):
        source_pos = first['members_offset'] + (i + 1) * 16
        target_pos = first['members_offset'] + i * 16
        result[target_pos:target_pos + 16] = result[source_pos:source_pos + 16]
    last = first['members_offset'] + (len(members) - 1) * 16
    result[last:last + 16] = b'\0' * 16
    remaining = [member for i, member in enumerate(members) if i != index]
    offset = 0
    for i, member in enumerate(remaining):
        pos = first['members_offset'] + i * 16
        name_ptr = struct.unpack_from('<Q', result, pos)[0]
        struct.pack_into('<Q I H H', result, pos, name_ptr, member['name_hash_raw'], offset, member['byte_size'])
        offset += member['byte_size']
    new_size = (offset + 15) & ~15
    for entry in entries:
        struct.pack_into('<I', result, entry['offset'] + 16, new_size)
        struct.pack_into('<I', result, entry['offset'] + 20, len(remaining))
    return bytes(result)


def _add_table_resource_bytes(data: bytes, kind: str, record_index: int, resource_name: str, source: Path) -> bytes:
    """向指定记录共享的 SRV/Sampler/UAV 表追加一个 16 字节资源描述符。"""
    if kind not in ('srvs', 'samplers', 'uavs'):
        raise EditError('只允许编辑 SRV、Sampler 或 UAV 资源表。')
    _, info = inspect_data(data, source)
    if not isinstance(record_index, int) or not 0 <= record_index < len(info['records']):
        raise EditError('MMTR 记录索引无效。')
    record = info['records'][record_index]
    table_offsets = record['metadata_tables'][kind]
    if not table_offsets:
        raise EditError('目标记录没有可扩展的资源表；当前版本不创建新的阶段绑定表。')
    table_offset = table_offsets[0]
    table_entries = [entry for entry in info['metadata'][kind] if table_offset <= entry['offset'] < table_offset + len(table_offsets) * 16]
    if len(table_entries) != len(table_offsets):
        raise EditError('目标资源表无法完整解析，拒绝修改。')
    if not resource_name or any(not c.isascii() or c in '\x00\r\n' for c in resource_name):
        raise EditError('MMTR 资源名称必须是非空 ASCII。')
    if any(entry['name'] == resource_name for entry in table_entries):
        raise EditError(f'MMTR 资源已存在：{resource_name}')
    old_start = info['shader_start']
    insert_pos = table_offset + len(table_offsets) * 16
    name_bytes = resource_name.encode('ascii') + b'\0'
    head_delta, name_delta = 16, len(name_bytes)
    prefix = bytearray(data[:insert_pos])
    entry_pos = len(prefix)
    prefix.extend(struct.pack('<QII', 0, murmur3_x86_32(resource_name.encode('ascii'), 0xffffffff), 0))
    prefix.extend(data[insert_pos:old_start])
    new_name_offset = len(prefix)
    prefix.extend(name_bytes)
    new_shader_start = len(prefix)
    locations = _metadata_pointer_locations(info)
    for old_location in locations:
        new_location = old_location + head_delta if old_location >= insert_pos else old_location
        if new_location + 8 > len(prefix):
            continue
        old_value = struct.unpack_from('<Q', data, old_location)[0]
        struct.pack_into('<Q', prefix, new_location, _shift_pointer(old_value, insert_pos, head_delta + name_delta if old_value >= old_start else head_delta))
    struct.pack_into('<Q', prefix, entry_pos, new_name_offset)
    pointer_field, count_field, _width = TABLE_FIELDS[kind]
    for candidate in info['records']:
        old_pointer = struct.unpack_from('<Q', data, candidate['offset'] + pointer_field)[0]
        if old_pointer == table_offset:
            new_record_offset = candidate['offset'] + head_delta if candidate['offset'] >= insert_pos else candidate['offset']
            struct.pack_into('<B', prefix, new_record_offset + count_field, len(table_offsets) + 1)
    struct.pack_into('<Q', prefix, info['record_start'] - 8, new_shader_start)
    return bytes(prefix) + data[old_start:]


def _remove_table_resource_bytes(data: bytes, kind: str, record_index: int, resource_name: str, source: Path) -> bytes:
    """从指定记录共享的资源表删除一个描述符并更新所有引用记录。"""
    if kind not in ('srvs', 'samplers', 'uavs'):
        raise EditError('只允许编辑 SRV、Sampler 或 UAV 资源表。')
    _, info = inspect_data(data, source)
    if not isinstance(record_index, int) or not 0 <= record_index < len(info['records']):
        raise EditError('MMTR 记录索引无效。')
    record = info['records'][record_index]
    table_offsets = record['metadata_tables'][kind]
    if not table_offsets:
        raise EditError('目标记录没有资源表。')
    table_offset = table_offsets[0]
    entries = [entry for entry in info['metadata'][kind] if table_offset <= entry['offset'] < table_offset + len(table_offsets) * 16]
    index = next((i for i, entry in enumerate(entries) if entry['name'] == resource_name), None)
    if index is None:
        raise EditError(f'MMTR 资源不存在：{resource_name}')
    result = bytearray(data)
    for i in range(index, len(entries) - 1):
        src = table_offset + (i + 1) * 16
        dst = table_offset + i * 16
        result[dst:dst + 16] = result[src:src + 16]
    result[table_offset + (len(entries) - 1) * 16:table_offset + len(entries) * 16] = b'\0' * 16
    pointer_field, count_field, _width = TABLE_FIELDS[kind]
    for candidate in info['records']:
        old_pointer = struct.unpack_from('<Q', data, candidate['offset'] + pointer_field)[0]
        if old_pointer == table_offset:
            struct.pack_into('<B', result, candidate['offset'] + count_field, len(entries) - 1)
            if len(entries) == 1:
                struct.pack_into('<Q', result, candidate['offset'] + pointer_field, 0)
    return bytes(result)


class Document:
    def __init__(self, data: bytes, path: Path):
        self.source = Path(path).resolve()
        self.original = bytes(data)
        try:
            _, self.info = inspect_data(self.original, self.source)
            groups = self.info['header_u16_04']
            # 只放开各游戏已研究的组数；声明记录必须完整，不能接受兼容扫描的截断结果。
            allowed = {g for game, _size, groups, _keys, _layout in FORMATS.values() if game == self.info['game'] for g in groups}
            layout = self.info.get('header_layout', 'uniform')
            counts = self.info.get('program_counts') or [self.info['header_u16_06']] * groups
            # per-target 允许各目标程序数不同，组边界由 program_counts 决定；uniform 仍要求同名序列。
            sequence_ok = (layout == 'per-target'
                           or all(g['name_sequence_matches_first'] for g in self.info['candidate_groups']))
            counts_ok = (len(counts) == groups and sum(counts) == self.info['record_count']
                         and all(g['record_count'] == counts[i]
                                 for i, g in enumerate(self.info['candidate_groups'])))
            if (groups not in allowed or len(self.info['candidate_groups']) != groups
                    or not counts_ok or not sequence_ok):
                raise EditError('当前版本仅支持完整的同名序列 SDF/MMTR 布局（荒野四组或五组，RE4 三组，SF6 三组，旧版 DD2 三组，RE8 五组，RE2RT 五组，MHRISE 三组，RE2 四组，RE3 四组；per-target 四组：RE9/PRAGMATA/DD2/ONIMUSHA）。')
        except (ValueError, struct.error, IndexError, UnicodeError) as exc:
            raise EditError(f'无法打开 MMTR：{exc}') from exc
        self.replacements: dict[int, bytes] = {}
        self.renames: dict[int, str] = {}
        self.redirects: dict[tuple[int, int], int] = {}
        self.metadata_edits: list[dict] = []
        self.replacement_notes: dict[int, dict] = {}
        self.by_offset = {s['offset']: s['index'] for s in self.info['shaders']}
        self.names = {}
        self.name_rows = {}
        for record in self.info['records']:
            self.names[record['name_offset']] = record['name']
        for kind, entries in self.info['metadata'].items():
            for entry in entries:
                rows = [entry] + (entry['members'] if kind == 'cbuffers' else [])
                for row in rows:
                    self.names[row['name_offset']] = row['name']
                    self.name_rows[row['offset']] = row
        self.integrity = self.check_original_integrity()

    @classmethod
    def open(cls, path):
        path = Path(path)
        return cls(path.read_bytes(), path)

    @property
    def dirty(self):
        return bool(self.replacements or self.renames or self.redirects or self.metadata_edits)

    def check_original_integrity(self):
        bad_names = [r['offset'] for r in self.name_rows.values()
                     if murmur3_x86_32(r['name'].encode('ascii'), 0xffffffff) != r['name_hash_raw']]
        if self.info.get('runtime_cache_keys', True):
            bad_shaders = [s['index'] for s in self.info['shaders']
                           if xxh64(self.original[s['offset']:s['trailer_offset']], 0xffffffff)
                           != int.from_bytes(bytes.fromhex(s['trailer_hex']), 'little')]
            checked = len(self.info['shaders'])
        else:
            bad_shaders, checked = [], 0
        return {'name_records_checked': len(self.name_rows), 'name_hash_mismatches': bad_names,
                'shader_trailers_checked': checked,
                'shader_trailer_mismatches': bad_shaders}

    def _index(self, value):
        if not isinstance(value, int) or not 0 <= value < len(self.info['shaders']):
            raise EditError('着色器索引无效。')

    def shader_bytes(self, index):
        self._index(index)
        s = self.info['shaders'][index]
        return self.replacements.get(index, self.original[s['offset']:s['trailer_offset']])

    def shader_name(self, pointer):
        return self.renames.get(pointer, self.names.get(pointer, ''))

    def current_info(self):
        """返回含待导出 metadata 编辑的当前结构快照。"""
        if not self.metadata_edits:
            return self.info
        self.build()
        return self._build_cache[3]

    def interface_differences(self, index, data):
        self._index(index)
        new = validate_shader(data)
        old = self.info['shaders'][index]
        if new['stage'] != old['stage'] or new['shader_model'] != old['shader_model']:
            raise EditError(f"阶段和 Shader Model 必须保持 {old['stage'].upper()} / {old['shader_model']}。")
        # 接口比较必须针对当前工作副本；此前已替换过的 Shader 不能退回原始 blob 比较。
        left = interface_chunks(self.shader_bytes(index))
        right = interface_chunks(data)
        return [tag for tag in sorted(left.keys() | right.keys()) if left.get(tag) != right.get(tag)]

    def replace_shader(self, index, data, *, allow_interface_change=False, note=None):
        differences = self.interface_differences(index, data)
        if differences and not allow_interface_change:
            raise EditError('接口/功能 chunk 不同：' + ', '.join(differences) + '。请检查兼容性，或启用专家选项。')
        original = self.info['shaders'][index]
        if hashlib.sha256(data).hexdigest() == original['sha256']:
            self.replacements.pop(index, None)
            self.replacement_notes.pop(index, None)
        else:
            self.replacements[index] = bytes(data)
            self.replacement_notes[index] = {'interface_chunk_differences': differences,
                                             'expert_override': allow_interface_change, **(note or {})}

    def rename(self, pointer, name):
        if pointer not in self.names:
            raise EditError('名称指针无效。')
        try:
            raw = name.encode('ascii')
        except UnicodeError as exc:
            raise EditError('名称必须是 ASCII。') from exc
        old = self.names[pointer]
        if not raw or len(raw) > len(old) or any(c < 33 or c > 126 for c in raw):
            raise EditError(f'名称应为非空 ASCII，长度最多 {len(old)} 字节，不含空格。')
        # No moving strings or editing overlapping suffix references in this version.
        if any(p != pointer and max(p, pointer) < min(p + len(n) + 1, pointer + len(old) + 1)
               for p, n in self.names.items()):
            raise EditError('该字符串存在重叠引用，当前版本不允许改名。')
        if name == old:
            self.renames.pop(pointer, None)
        else:
            self.renames[pointer] = name

    def effective_shader(self, record_index, field):
        record = self.info['records'][record_index]
        return self.redirects.get((record_index, field), next(
            (r['shader_index'] for r in record['shader_references'] if r['field'] == field), None))

    def redirect(self, record_index, field, target, *, allow_interface_change=False):
        self._index(target)
        if not 0 <= record_index < self.info['record_count'] or field not in SHADER_FIELDS:
            raise EditError('通道或引用字段无效。')
        record = self.info['records'][record_index]
        original = next((r for r in record['shader_references'] if r['field'] == field), None)
        if original is None:
            raise EditError('当前版本只能重定向已有阶段，不能新增阶段或修改资源表。')
        source_index = original['shader_index']
        differences = self.interface_differences(source_index, self.shader_bytes(target))
        if differences and not allow_interface_change:
            raise EditError('目标着色器接口不同：' + ', '.join(differences))
        fields = [field]
        # The repeated VS fields observed at +08/+10 must stay consistent.
        if field in (8, 16):
            fields = [r['field'] for r in record['shader_references'] if r['field'] in (8, 16)]
        for f in fields:
            previous = next(r['shader_index'] for r in record['shader_references'] if r['field'] == f)
            if target == previous:
                self.redirects.pop((record_index, f), None)
            else:
                self.redirects[(record_index, f)] = target

    def references(self, index):
        self._index(index)
        return [(r['index'], ref['field']) for r in self.info['records']
                for ref in r['shader_references']
                if self.effective_shader(r['index'], ref['field']) == index]

    def reset(self):
        self.replacements.clear()
        self.renames.clear()
        self.redirects.clear()
        self.metadata_edits.clear()
        self.replacement_notes.clear()

    def add_cbuffer_member(self, cbuffer: str, name: str, byte_size: int, byte_offset: int) -> None:
        """暂存 UserMaterial 等 cbuffer 成员新增，导出时同步所有 metadata 描述符。"""
        if not isinstance(cbuffer, str) or not cbuffer:
            raise EditError('常量缓冲名称无效。')
        _add_cbuffer_member_bytes(self.build()[0], cbuffer, name, byte_size, byte_offset, self.source)
        self.metadata_edits.append({'kind': 'add_cbuffer_member', 'cbuffer': cbuffer, 'name': name,
                                    'byte_size': byte_size, 'byte_offset': byte_offset})

    def remove_cbuffer_member(self, cbuffer: str, name: str) -> None:
        """暂存 cbuffer 成员删除，导出时压缩共享成员表并更新大小。"""
        _remove_cbuffer_member_bytes(self.build()[0], cbuffer, name, self.source)
        self.metadata_edits.append({'kind': 'remove_cbuffer_member', 'cbuffer': cbuffer, 'name': name})

    def add_resource(self, kind: str, record_index: int, name: str) -> None:
        """暂存资源表新增；只扩展已有记录的非空资源表。"""
        _add_table_resource_bytes(self.build()[0], kind, record_index, name, self.source)
        self.metadata_edits.append({'kind': 'add_resource', 'resource_kind': kind,
                                    'record_index': record_index, 'name': name})

    def remove_resource(self, kind: str, record_index: int, name: str) -> None:
        """暂存资源表删除，并同步使用该共享表的记录计数。"""
        _remove_table_resource_bytes(self.build()[0], kind, record_index, name, self.source)
        self.metadata_edits.append({'kind': 'remove_resource', 'resource_kind': kind,
                                    'record_index': record_index, 'name': name})

    def validate_metadata_interfaces(self, dll: str | Path, *, _in_worker=False, _reflections=None) -> dict:
        """用 DXC 反射核对待导出 metadata 与实际 DXIL 接口。

        该检查只读取当前文档和 Shader blob，不执行着色器；新增/删除接口
        仍必须由调用方先提供对应的编译结果，才能通过这里的闭环验证。
        """
        if not self.metadata_edits:
            return {'status': 'skipped', 'scope': 'metadata_delta', 'reason': 'no_metadata_edits',
                    'valid': None, 'checked_shaders': 0, 'checked_records': 0,
                    'issues': [], 'metadata_edits': 0,
                    'coverage': {'associated_layout': False, 'material_markers': False}, 'game_tested': False}
        if not _in_worker:
            from .material_process import run_material_task
            key = self._validation_key(dll)
            cached = getattr(self, '_validation_cache', None)
            if cached is not None and cached[0] == key:
                return copy.deepcopy(cached[1])
            result = run_material_task(self, 'validate', dll)
            self._validation_cache = (key, copy.deepcopy(result))
            return result
        try:
            from .dxc import Dxc
            from inspect_mmtr import reflection
            decoder = Dxc(dll)
        except Exception as exc:
            raise EditError(f'无法加载 DXC 进行接口验证：{exc}') from exc
        try:
            _, current = inspect_data(self.build()[0], self.source)
            reflections = {}
            for shader in current['shaders']:
                index = shader['index']
                payload = self.shader_bytes(index)
                cached = (_reflections or {}).get(index)
                # 只复用当前隔离任务已解析且字节hash完全匹配的反射；变更后必须重新读取。
                if cached is not None and cached[0] == hashlib.sha256(payload).digest():
                    reflections[index] = cached[1]
                else:
                    reflections[index] = reflection(decoder.disassemble(payload))
        except Exception as exc:
            if isinstance(exc, EditError):
                raise
            raise EditError(f'DXC 反射失败，未完成接口验证：{exc}') from exc
        finally:
            decoder.close()

        issues: list[dict] = []
        changed_cbuffers = {edit['cbuffer'] for edit in self.metadata_edits
                            if edit['kind'] in ('add_cbuffer_member', 'remove_cbuffer_member')}
        extensions = [edit for edit in self.metadata_edits if edit['kind'] in ('extend_material_interface', 'remove_material_interface')]
        if any(edit['kind'] == 'remove_material_interface' and edit['layout']['removed_parameters'] for edit in extensions):
            changed_cbuffers.add('UserMaterial')
        if any(edit.get('parameter') or edit.get('parameters') for edit in extensions):
            changed_cbuffers.add('UserMaterial')
        # 自动迁移证据绑定实际Shader字节；后续替换或手动修改不得复用旧通过结论。
        if extensions:
            if any(edit['kind'] not in ('extend_material_interface', 'remove_material_interface') for edit in self.metadata_edits):
                issues.append({'kind': 'material_layout', 'problem': '自动扩展后不能混用未验证的手动metadata修改。'})
            redirects = [[record, field, target] for (record, field), target in sorted(self.redirects.items())]
            if redirects != extensions[-1]['redirects']:
                issues.append({'kind': 'material_layout', 'problem': '接口扩展后变体引用已改变，需要重新核对绑定映射。'})
            proof = extensions[-1]['shader_hashes']
            for shader in current['shaders']:
                index = shader['index']
                if proof.get(str(index)) != hashlib.sha256(self.shader_bytes(index)).hexdigest():
                    issues.append({'kind': 'material_layout', 'shader_index': index,
                                   'problem': '接口扩展后Shader已改变，需重新验证材质地址迁移。'})
        elif 'UserMaterial' in changed_cbuffers:
            from .material_addresses import material_reads
            # 旧手动同步只验证具名反射，不能证明bindless/raw及实例流跟随布局迁移。
            decoder = Dxc(dll)
            try:
                for shader in current['shaders']:
                    try:
                        reads = material_reads(decoder.disassemble(self.shader_bytes(shader['index'])))
                    except ValueError as exc:
                        issues.append({'kind': 'material_layout', 'shader_index': shader['index'],
                                       'problem': f'材质地址形状尚未验证：{exc}'})
                        break
                    if reads:
                        issues.append({'kind': 'material_layout', 'shader_index': shader['index'],
                                       'problem': 'UserMaterial布局变化尚未同步材质尾部/实例地址，请使用关联MDF自动新增。'})
                        break
            finally:
                decoder.close()
        # UserMaterial 是 MDF 供值的核心接口，名称和偏移必须与每个使用它的 DXIL 完全一致。
        for cbuffer_name in changed_cbuffers:
            metadata_entry = next((entry for entry in current['metadata']['cbuffers']
                                   if entry['name'] == cbuffer_name), None)
            expected = ({member['name']: member['byte_offset']
                         for member in metadata_entry['members']}
                        if metadata_entry else {})
            expected_size = metadata_entry['buffer_size'] if metadata_entry else None
            for shader in current['shaders']:
                reflected = next((cb for cb in reflections[shader['index']]['cbuffers']
                                  if cb['name'] == cbuffer_name), None)
                if reflected is None:
                    continue
                actual = {variable['name'].removeprefix('VAR_'): variable['offset']
                          for variable in reflected['variables']}
                if actual != expected or reflected['size'] != expected_size:
                    issues.append({
                        'kind': 'cbuffer', 'shader_index': shader['index'],
                        'name': cbuffer_name, 'expected': expected,
                        'actual': actual, 'expected_size': expected_size,
                        'actual_size': reflected['size'],
                    })

        # 按元数据偏移建索引，避免每条记录重新遍历整张名称表。
        indexes = {id(info): {kind: {entry['offset']: entry['name'] for entry in info['metadata'][kind]}
                              for kind in ('srvs', 'samplers', 'uavs')} for info in (self.info, current)}

        def table_names(info: dict, record: dict, kind: str) -> set[str]:
            names = indexes[id(info)][kind]
            return {names[offset] for offset in record['metadata_tables'][kind]}

        kind_map = {'texture': 'srvs', 'sampler': 'samplers', 'UAV': 'uavs'}
        original_info = self.info
        for record in current['records']:
            original_record = original_info['records'][record['index']]
            for kind in ('srvs', 'samplers', 'uavs'):
                added = table_names(current, record, kind) - table_names(original_info, original_record, kind)
                removed = table_names(original_info, original_record, kind) - table_names(current, record, kind)
                if not added and not removed:
                    continue
                if any(edit['kind'] in ('add_resource', 'remove_resource') for edit in self.metadata_edits):
                    issues.append({'kind': kind, 'record_index': record['index'],
                                   'problem': '手动资源表编辑尚未验证绑定映射及阶段计数，禁止作为可运行接口导出。'})
                reflected_names: set[str] = set()
                for reference in record['shader_references']:
                    for row in reflections[reference['shader_index']]['resource_binding_rows']:
                        fields = row.split()
                        if len(fields) >= 2 and kind_map.get(fields[1]) == kind:
                            reflected_names.add(fields[0])
                for name in sorted(added):
                    if name not in reflected_names:
                        issues.append({'kind': kind, 'record_index': record['index'],
                                       'name': name, 'problem': 'metadata 新增资源未在关联 DXIL 声明'})
                for name in sorted(removed):
                    if name in reflected_names:
                        issues.append({'kind': kind, 'record_index': record['index'],
                                       'name': name, 'problem': 'metadata 删除资源仍被关联 DXIL 声明'})
        return {'status': 'failed' if issues else 'passed', 'scope': 'metadata_delta',
                'valid': not issues, 'checked_shaders': len(reflections),
                'checked_records': len(current['records']), 'issues': issues,
                'metadata_edits': len(self.metadata_edits),
                'coverage': {'associated_layout': False, 'material_markers': False}, 'game_tested': False}

    def sync_compiled_interface(self, index: int, payload: bytes, dll: str | Path) -> dict:
        """应用一个显式编译结果，并暂存可安全推断的 metadata 新增。

        只自动处理已有 cbuffer 的尾部成员和已有资源表的新增项；删除资源、
        改变既有成员布局或创建新阶段表都要求调用方先处理，避免批量变体被误改。
        """
        self._index(index)
        differences = self.interface_differences(index, payload)
        old_shader = self.info['shaders'][index]
        try:
            from .dxc import Dxc
            from inspect_mmtr import reflection
            decoder = Dxc(dll)
        except Exception as exc:
            raise EditError(f'无法加载 DXC 进行接口同步：{exc}') from exc
        try:
            old_ref = reflection(decoder.disassemble(self.shader_bytes(index)))
            new_ref = reflection(decoder.disassemble(payload))
        except Exception as exc:
            raise EditError(f'DXC 反射失败，未应用编译结果：{exc}') from exc
        finally:
            decoder.close()

        info = self.current_info()
        metadata_ops: list[dict] = []
        blockers: list[str] = []

        def layout(cbuffer: dict) -> dict[str, tuple[int, int | None]]:
            return {variable['name'].removeprefix('VAR_'): (
                variable['offset'], self._reflection_member_size(variable['type']))
                    for variable in cbuffer['variables']}

        old_cbuffers = {cb['name']: cb for cb in old_ref['cbuffers']}
        new_cbuffers = {cb['name']: cb for cb in new_ref['cbuffers']}
        metadata_cbuffers = {cb['name']: cb for cb in info['metadata']['cbuffers']}
        for name, new_cb in new_cbuffers.items():
            old_cb = old_cbuffers.get(name)
            metadata_cb = metadata_cbuffers.get(name)
            if old_cb is None or metadata_cb is None:
                if old_cb != new_cb and name not in metadata_cbuffers:
                    blockers.append(f'无法为新 cbuffer {name} 创建 MMTR 描述符')
                continue
            old_layout, new_layout = layout(old_cb), layout(new_cb)
            current_layout = {member['name']: (member['byte_offset'], member['byte_size'])
                              for member in metadata_cb['members']}
            if old_layout != current_layout:
                blockers.append(f'{name} 的原 Shader 与当前 MMTR metadata 已不一致')
                continue
            changed_existing = [member for member in current_layout
                                 if member in new_layout and current_layout[member][0] != new_layout[member][0]]
            if changed_existing:
                blockers.append(f'{name} 改变了已有成员偏移：{", ".join(changed_existing)}')
                continue
            removed = [member for member in current_layout if member not in new_layout]
            if removed:
                first_removed = min(next(i for i, item in enumerate(metadata_cb['members'])
                                         if item['name'] == member) for member in removed)
                if any(item['name'] not in removed for item in metadata_cb['members'][first_removed:]):
                    blockers.append(f'{name} 只支持删除末尾成员，不能自动压缩中间布局')
                else:
                    blockers.append(f'{name} 删除成员请先在 Metadata 页确认并同步所有变体')
            additions = [variable for variable in new_cb['variables']
                         if variable['name'].removeprefix('VAR_') not in current_layout]
            for variable in additions:
                member_name = variable['name'].removeprefix('VAR_')
                byte_size = self._reflection_member_size(variable['type'])
                if byte_size is None:
                    blockers.append(f'无法推断 {name}.{member_name} 的字节宽度')
                    continue
                if variable['offset'] < metadata_cb['buffer_size']:
                    blockers.append(f'{name}.{member_name} 不是末尾新增成员')
                    continue
                metadata_ops.append({'kind': 'add_cbuffer_member', 'cbuffer': name,
                                     'name': member_name, 'byte_size': byte_size,
                                     'byte_offset': variable['offset']})

        def resource_names(reflection_data: dict, kind: str) -> set[str]:
            expected = {'srvs': 'texture', 'samplers': 'sampler', 'uavs': 'UAV'}[kind]
            return {row.split()[0] for row in reflection_data['resource_binding_rows']
                    if len(row.split()) >= 2 and row.split()[1] == expected}

        old_resources = {kind: resource_names(old_ref, kind) for kind in ('srvs', 'samplers', 'uavs')}
        new_resources = {kind: resource_names(new_ref, kind) for kind in ('srvs', 'samplers', 'uavs')}
        affected_records: set[int] = set()
        planned_resource_tables: set[tuple[str, tuple[int, ...], str]] = set()
        for record in info['records']:
            if not any(reference['shader_index'] == index for reference in record['shader_references']):
                continue
            affected_records.add(record['index'])
            for kind in ('srvs', 'samplers', 'uavs'):
                additions = new_resources[kind] - old_resources[kind]
                removals = old_resources[kind] - new_resources[kind]
                table_names = {entry['name'] for entry in info['metadata'][kind]
                               if entry['offset'] in set(record['metadata_tables'][kind])}
                for name in sorted(removals):
                    blockers.append(f'{kind} {name} 需要手动确认共享记录后删除')
                for name in sorted(additions - table_names):
                    if not record['metadata_tables'][kind]:
                        blockers.append(f'记录 {record["index"]} 没有可扩展的 {kind} 表')
                    elif (kind, tuple(record['metadata_tables'][kind]), name) not in planned_resource_tables:
                        planned_resource_tables.add((kind, tuple(record['metadata_tables'][kind]), name))
                        metadata_ops.append({'kind': 'add_resource', 'resource_kind': kind,
                                             'record_index': record['index'], 'name': name})
        if blockers:
            raise EditError('接口同步需要人工处理：' + '；'.join(dict.fromkeys(blockers)))

        previous_edits = list(self.metadata_edits)
        previous_replacements = dict(self.replacements)
        previous_notes = dict(self.replacement_notes)
        try:
            for operation in metadata_ops:
                if operation['kind'] == 'add_cbuffer_member':
                    self.add_cbuffer_member(operation['cbuffer'], operation['name'],
                                            operation['byte_size'], operation['byte_offset'])
                else:
                    self.add_resource(operation['resource_kind'], operation['record_index'], operation['name'])
            self.replace_shader(index, payload, allow_interface_change=True,
                                note={'dxc_disassembly_ok': True, 'interface_sync': True,
                                      'interface_chunk_differences': differences})
        except Exception:
            self.metadata_edits = previous_edits
            self.replacements = previous_replacements
            self.replacement_notes = previous_notes
            raise
        affected_shader_indices = {index}
        for record in info['records']:
            if record['index'] in affected_records:
                affected_shader_indices.update(reference['shader_index']
                                               for reference in record['shader_references'])
        return {'shader_index': index, 'metadata_operations': metadata_ops,
                'affected_records': sorted(affected_records),
                'affected_shader_indices': sorted(affected_shader_indices),
                'requires_other_shader_variants': bool(metadata_ops),
                'interface_chunk_differences': differences}

    def sync_compiled_interfaces(self, payloads, dll: str | Path) -> dict:
        """在一个事务中同步多个受影响 Shader 及其共享 MMTR 接口。

        ``payloads`` 是 ``{shader_index: DXBC bytes}`` 或同等键值序列。候选文档只在
        所有共享 cbuffer/资源表的关联 Shader 都已提供并通过 DXC 反射后提交；缺少
        变体时返回待补齐列表，保留调用方当前 metadata、替换字节和回滚记录不变。
        该方法只做离线结构与 DXIL/RDEF 校验，不持有 GPU 资源，也不代表游戏运行时通过。
        """
        if hasattr(payloads, 'items'):
            entries = list(payloads.items())
        else:
            entries = [(item['index'], item['payload']) for item in payloads]
        if not entries:
            raise EditError('批量接口同步至少需要一个 Shader 编译结果。')
        normalized: dict[int, bytes] = {}
        for index, payload in entries:
            self._index(index)
            if index in normalized:
                raise EditError(f'Shader #{index} 在批量请求中重复。')
            if not isinstance(payload, (bytes, bytearray)):
                raise EditError(f'Shader #{index} 的编译结果不是字节数据。')
            normalized[index] = bytes(payload)

        # 用完整工作状态建立候选文档，确保任何失败都不会污染调用方状态。
        def clone_document() -> 'Document':
            clone = Document(self.original, self.source)
            clone.replacements = dict(self.replacements)
            clone.renames = dict(self.renames)
            clone.redirects = dict(self.redirects)
            clone.metadata_edits = [dict(edit) for edit in self.metadata_edits]
            clone.replacement_notes = {index: dict(note) for index, note in self.replacement_notes.items()}
            return clone

        candidate = clone_document()
        original_edit_count = len(self.metadata_edits)
        reports = []
        for index, payload in sorted(normalized.items()):
            # 每个 payload 都相对同一基线推断，避免第一个新增成员使后续旧布局
            # 看起来“不一致”；真正的 metadata 操作稍后统一去重并一次应用。
            probe = clone_document()
            reports.append(probe.sync_compiled_interface(index, payload, dll))

        planned_operations: dict[tuple, dict] = {}
        for report in reports:
            for operation in report['metadata_operations']:
                key = tuple(sorted(operation.items()))
                planned_operations[key] = operation
        metadata_operations = list(planned_operations.values())
        for operation in metadata_operations:
            if operation['kind'] == 'add_cbuffer_member':
                candidate.add_cbuffer_member(operation['cbuffer'], operation['name'],
                                             operation['byte_size'], operation['byte_offset'])
            else:
                candidate.add_resource(operation['resource_kind'], operation['record_index'], operation['name'])
        for index, payload in sorted(normalized.items()):
            differences = candidate.interface_differences(index, payload)
            candidate.replace_shader(index, payload, allow_interface_change=True,
                                     note={'dxc_disassembly_ok': True, 'interface_sync': True,
                                           'interface_chunk_differences': differences})
        affected: set[int] = set(normalized)
        for report in reports:
            affected.update(report['affected_shader_indices'])

        # cbuffer 描述符是全局共享的；反射到被修改 cbuffer 的每个 Shader 都必须同步。
        changed_cbuffers = {op['cbuffer'] for op in metadata_operations
                            if op['kind'] == 'add_cbuffer_member'}
        if changed_cbuffers:
            try:
                from .dxc import Dxc
                from inspect_mmtr import reflection
                decoder = Dxc(dll)
                try:
                    for shader in self.info['shaders']:
                        reflected = reflection(decoder.disassemble(self.shader_bytes(shader['index'])))
                        if any(cb['name'] in changed_cbuffers for cb in reflected['cbuffers']):
                            affected.add(shader['index'])
                finally:
                    decoder.close()
            except Exception as exc:
                raise EditError(f'无法确定共享 cbuffer 关联 Shader：{exc}') from exc

        missing = sorted(affected - set(normalized)) if metadata_operations else []
        if missing:
            return {
                'complete': False, 'applied': False,
                'submitted_shader_indices': sorted(normalized),
                'affected_shader_indices': sorted(affected),
                'missing_shader_indices': missing,
                'metadata_operations': metadata_operations,
                'shader_reports': reports,
            }

        # 全量反射验证候选状态；失败时候选对象直接丢弃，调用方状态保持原样。
        validation = candidate.validate_metadata_interfaces(dll)
        if validation['valid'] is False:
            first = validation['issues'][0]
            detail = first.get('problem') or f"{first.get('kind')} {first.get('name', '')}"
            raise EditError(f'批量接口同步验证失败：{detail}')

        self.replacements = candidate.replacements
        self.replacement_notes = candidate.replacement_notes
        self.metadata_edits = candidate.metadata_edits
        return {
            'complete': True, 'applied': True,
            'submitted_shader_indices': sorted(normalized),
            'affected_shader_indices': sorted(affected),
            'missing_shader_indices': [],
            'metadata_operations': metadata_operations,
            'shader_reports': reports,
            'validation': validation,
        }

    @staticmethod
    def _reflection_member_size(type_name: str) -> int | None:
        """把 DXC 反射的基础类型转换成 metadata 所需的字节宽度。"""
        value = re.sub(r'^(?:row_major|column_major)\s+', '', type_name).strip()
        array = re.search(r'\[(\d+)\]$', value)
        count = int(array[1]) if array else 1
        value = value[:array.start()] if array else value
        matrix = re.fullmatch(r'(?:float|uint|int|bool)(\d)x(\d)', value)
        if matrix:
            return int(matrix[1]) * int(matrix[2]) * 4 * count
        vector = re.fullmatch(r'(?:float|uint|int|bool)(\d)?', value)
        if vector:
            return (int(vector[1]) if vector[1] else 1) * 4 * count
        return None

    # 值键覆盖全部可变编辑容器；不依赖调用方记得递增revision或手动清空缓存。
    def _build_key(self):
        return (self.original, str(self.source), tuple(sorted(self.replacements.items())),
                tuple(sorted(self.renames.items())), tuple(sorted(self.redirects.items())),
                json.dumps([self.metadata_edits, self.replacement_notes, self.integrity], sort_keys=True))

    # 验证结论额外绑定DXC实际内容；更换DLL、替换Shader或改动证明都必须重新验证。
    def _validation_key(self, dll):
        path = Path(dll).resolve()
        return self._build_key(), str(path), hashlib.sha256(path.read_bytes()).digest()

    def remember_validation(self, dll, report):
        self._validation_cache = (self._validation_key(dll), copy.deepcopy(report))

    # 缓存只属于此文档，键覆盖全部编辑值；直接修改字典、回滚或切换源也不能复用旧候选。
    # 候选仍由完整重建/重解析产生，报告副本不与缓存或事务的metadata共享可变对象。
    def build(self):
        if not self.dirty:
            return self.original, {'unchanged': True, 'sha256': self.info['sha256'],
                                   'size': len(self.original), 'changed_shaders': []}
        key = self._build_key()
        cached = getattr(self, '_build_cache', None)
        if cached is None or cached[0] != key:
            data, report, info = self._build_uncached()
            cached = (key, data, copy.deepcopy(report), info)
            self._build_cache = cached
        return cached[1], copy.deepcopy(cached[2])

    def _build_uncached(self):
        if self.integrity['name_hash_mismatches'] or self.integrity['shader_trailer_mismatches']:
            raise EditError('原文件 hash 与当前研究格式不符；仅允许未编辑的原样导出。')
        if self.metadata_edits and self.renames:
            raise EditError('metadata 结构编辑与名称改名暂不能在同一次导出中混用。')
        working = self.original
        for edit in self.metadata_edits:
            if edit['kind'] == 'extend_material_interface':
                from .material_extension import append_metadata
                working = append_metadata(working, self.source, edit)
            elif edit['kind'] == 'remove_material_interface':
                from .removal_writer import remove_metadata
                working = remove_metadata(working, self.source, edit)
            elif edit['kind'] == 'add_cbuffer_member':
                working = _add_cbuffer_member_bytes(working, edit['cbuffer'], edit['name'], edit['byte_size'], edit['byte_offset'], self.source)
            elif edit['kind'] == 'remove_cbuffer_member':
                working = _remove_cbuffer_member_bytes(working, edit['cbuffer'], edit['name'], self.source)
            elif edit['kind'] == 'add_resource':
                working = _add_table_resource_bytes(working, edit['resource_kind'], edit['record_index'], edit['name'], self.source)
            else:
                working = _remove_table_resource_bytes(working, edit['resource_kind'], edit['record_index'], edit['name'], self.source)
        _, working_info = inspect_data(working, self.source)
        prefix = bytearray(working[:working_info['shader_start']])
        for pointer, name in self.renames.items():
            raw = name.encode('ascii')
            prefix[pointer:pointer + len(self.names[pointer]) + 1] = raw.ljust(len(self.names[pointer]) + 1, b'\0')
        for row in self.name_rows.values():
            if row['name_offset'] in self.renames:
                value = murmur3_x86_32(self.renames[row['name_offset']].encode('ascii'), 0xffffffff)
                struct.pack_into('<I', prefix, row['offset'] + 8, value)
        offsets, pieces, cursor = {}, [], len(prefix)
        changed = []
        runtime_keys = self.info.get('runtime_cache_keys', True)
        for s in self.info['shaders']:
            index = s['index']
            payload = self.shader_bytes(index)
            offsets[index] = cursor
            if index in self.replacements:
                tail = struct.pack('<Q', xxh64(payload, 0xffffffff)) if runtime_keys else b''
                changed.append({'index': index, 'old_size': s['size'], 'new_size': len(payload),
                                'old_offset': s['offset'], 'new_offset': cursor,
                                'sha256': hashlib.sha256(payload).hexdigest(),
                                **self.replacement_notes.get(index, {})})
            else:
                tail = self.original[s['trailer_offset']:s['trailer_offset'] + 8] if runtime_keys else b''
            pieces.extend((payload, tail))
            cursor += len(payload) + (8 if runtime_keys else 0)
        for r in working_info['records']:
            for ref in r['shader_references']:
                target = self.effective_shader(r['index'], ref['field'])
                struct.pack_into('<Q', prefix, r['offset'] + ref['field'], offsets[target])
                struct.pack_into('<I', prefix, r['offset'] + 0xB8 + (ref['field'] - 8) // 2,
                                 len(self.shader_bytes(target)))
        rebuilt = bytes(prefix) + b''.join(pieces)
        # Parse the actual rebuilt bytes, not our planned relocation map.
        _, parsed = inspect_data(rebuilt, self.source)
        if parsed['record_count'] != self.info['record_count'] or parsed['shader_count'] != self.info['shader_count']:
            raise EditError('导出后的结构计数发生意外变化。')
        for s in parsed['shaders']:
            i = s['index']
            if rebuilt[s['offset']:s['trailer_offset']] != self.shader_bytes(i):
                raise EditError('导出后的着色器切片校验失败。')
        report = {'unchanged': False, 'source': str(self.source), 'source_sha256': self.info['sha256'],
                  'sha256': hashlib.sha256(rebuilt).hexdigest(), 'size': len(rebuilt),
                  'changed_shaders': changed, 'renames': [{'pointer': p, 'old': self.names[p], 'new': n}
                                                        for p, n in self.renames.items()],
                  'redirects': [{'record': r, 'field': f, 'target_shader': s}
                                for (r, f), s in sorted(self.redirects.items())],
                  'metadata_edits': list(self.metadata_edits),
                  'reparse_valid': True, 'runtime_tested': False,
                  'limitations': (['Only proven UserMaterial tail/PS Texture2D layouts are migrated.',
                                   'DXIL IR is not original HLSL; offline DXC checks do not prove runtime appearance.']
                                  if any(e['kind'] in ('extend_material_interface', 'remove_material_interface') for e in self.metadata_edits) else
                                  ['Unknown metadata preserved; only known shader references relocated.',
                                   'DXIL IR is not original HLSL. Resource tables are not regenerated.',
                                   'Internal DXBC checksum/DXIL validation is not implemented by the rebuilder.'])}
        return rebuilt, report, parsed

    def export(self, path):
        path = Path(path).resolve()
        if path == self.source or (path.exists() and self.source.exists() and os.path.samefile(path, self.source)):
            raise EditError('请另存为新文件，不能覆盖打开的原始 MMTR。')
        data, report = self.build()
        atomic_write(path, data)
        return report

    def export_report(self, path, report):
        path = Path(path).resolve()
        if path == self.source or (path.exists() and self.source.exists() and os.path.samefile(path, self.source)):
            raise EditError('报告不能覆盖原始 MMTR。')
        atomic_write(path, json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
