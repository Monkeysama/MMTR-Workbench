"""SDF Program（荒野与鬼武者 0x110、RE4 0x108）的只读结构与绑定索引，无 UI/DXC 或写入能力。

字段参考 REasy 0eb38af 的公开格式定义，独立实现；保留原字节与未知值。
旧 inspect_data 仍是兼容解析入口，新解释的问题只进入诊断，不改变其浏览行为。
"""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import struct

from inspect_mmtr import FORMATS, inspect_data

STAGES = ('vs', 'hs', 'ds', 'gs', 'ps', 'cs')
TABLES = ('cbuffers', 'samplers', 'srvs', 'uavs', 'optional')
COUNTS = (0xF6, 0xF7, 0xFC, 0xFD, 0xFE)
TYPES = ({255}, {0}, {0, 1, 2, 3, 4, 5, 6, 128}, {12, 13, 14, 15, 16, 128}, set())
KNOWN_VERSIONS = set(FORMATS)  # 只登记有原版语料核对的资源版本；版本来自文件名而非 header。


@dataclass(frozen=True)
class Diagnostic:
    """新增解释的诊断，保留 Program 和绝对来源位置，不影响旧解析器。"""
    severity: str
    code: str
    program: int | None
    field_offset: int
    message: str


@dataclass(frozen=True)
class PointerUse:
    """某个 Program 通过哪个指针字段使用对象；由外层串行会话生成。"""
    program: int
    field_offset: int
    role: str


@dataclass(frozen=True)
class RawObject:
    """按类别与物理范围识别共享对象，持有不可变原字节及完整使用者。"""
    identity: str
    kind: str
    offset: int
    size: int
    raw: bytes
    values: tuple
    users: tuple[PointerUse, ...]


@dataclass(frozen=True)
class ResourceBinding:
    """8B 绑定描述符；阶段 slot/mask/type 保留原值，不声明编辑支持。"""
    kind: str
    index: int
    offset: int
    metadata_offset: int
    name: str | None
    slots: tuple[int, ...]
    stage_mask: int
    type_code: int
    raw: bytes


@dataclass(frozen=True)
class CbufferMember:
    """既有常量缓冲成员，保存名称来源、原始 hash 和字节布局。"""
    offset: int
    name: str
    name_pointer: int
    name_hash_raw: int
    byte_offset: int
    byte_size: int
    raw: bytes


@dataclass(frozen=True)
class Metadata:
    """兼容解析器已识别的 metadata；成员字节由当前候选拥有。"""
    kind: str
    offset: int
    name: str
    name_pointer: int
    name_hash_raw: int
    reserved_raw: int
    buffer_size: int | None
    members_pointer: int | None
    members: tuple[CbufferMember, ...]
    raw: bytes


@dataclass(frozen=True)
class TableRef:
    """一个 Program 中绑定表与 metadata 表的对应关系及计数。"""
    kind: str
    binding_identity: str | None
    metadata_identity: str | None
    count: int
    expected_counts: tuple[int, ...]
    bindings: tuple[ResourceBinding, ...]


@dataclass(frozen=True)
class ShaderRef:
    """同时记录 legacy 阶段槽和载荷实际阶段，不推断运行时选择。"""
    field_offset: int
    lane: str
    shader_index: int
    offset: int
    size: int
    stage: str


@dataclass(frozen=True)
class Program:
    """单个记录（0x110 或 0x108）的只读投影，原字节负责保留所有未知字段。"""
    index: int
    target: int
    offset: int
    name: str
    raw: bytes
    shaders: tuple[ShaderRef, ...]
    tables: tuple[TableRef, ...]
    objects: tuple[str, ...]
    total_stage_bindings: int
    compute_group: tuple[int, ...]
    topology_raw: int
    reserved_hex: str


@dataclass(frozen=True)
class Header:
    """四/五组布局的 header，资源版本只从文件名提取，组数不代表资源类别。"""
    raw: bytes
    group_count: int
    programs_per_group: int
    shader_start: int
    record_end: int
    resource_version: int | None


# 快照只持有不可变字节/元组；索引按物理范围区分身份，不按资源名称去重。
@dataclass(frozen=True)
class Structure:
    sha256: str
    source_bytes: bytes
    header: Header
    programs: tuple[Program, ...]
    objects: tuple[RawObject, ...]
    metadata: tuple[Metadata, ...]
    shader_users: tuple[tuple[PointerUse, ...], ...]
    diagnostics: tuple[Diagnostic, ...]
    unmodeled_ranges: tuple[tuple[int, int], ...]

    def report(self, view='summary', index=None, offset=0, limit=40):
        """返回有界 JSON 视图；查询不会编译、写文件或改变快照。"""
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError('offset 必须非负，limit 必须为 1..100')
        if view == 'program':
            if index is None or not 0 <= index < len(self.programs):
                raise ValueError('Program 索引超出范围')
            program = self.programs[index]
            # 单 Program 最多 255 条/表，同样分页，避免大响应。
            result = public(program)
            for table in result['tables']:
                table['bindings'] = page(table['bindings'], offset, limit)
            result['objects'] = page(result['objects'], offset, limit)
            return result
        if view == 'shader_users':
            if index is None or not 0 <= index < len(self.shader_users):
                raise ValueError('Shader 索引超出范围')
            return page([public(x) for x in self.shader_users[index]], offset, limit)
        if view == 'objects':
            rows = []
            for index, obj in enumerate(self.objects[offset:offset+limit], offset):
                values = dict(obj.values)
                if 'elements_raw' in values:
                    values['elements_raw'] = page(list(values['elements_raw']), 0, limit)
                row = {'index': index, 'identity': obj.identity, 'kind': obj.kind,
                       'offset': obj.offset, 'size': obj.size,
                       'raw_preview_hex': obj.raw[:256].hex(), 'raw_sha256': hashlib.sha256(obj.raw).hexdigest(),
                       'raw_truncated': len(obj.raw) > 256, 'values': values,
                       'users': page([public(u) for u in obj.users[:limit]], 0, limit)}
                row['users']['total'] = len(obj.users)
                row['users']['next_offset'] = limit if limit < len(obj.users) else None
                rows.append(row)
            return {'total': len(self.objects), 'offset': offset, 'items': rows,
                    'next_offset': offset+limit if offset+limit < len(self.objects) else None}
        if view == 'object_users':
            if index is None or not 0 <= index < len(self.objects):
                raise ValueError('Object 索引超出范围')
            return {'identity': self.objects[index].identity,
                    **page([public(x) for x in self.objects[index].users], offset, limit)}
        if view == 'diagnostics':
            return page([public(x) for x in self.diagnostics], offset, limit)
        if view == 'unmodeled_ranges':
            return page([{'offset': start, 'size': size} for start, size in self.unmodeled_ranges], offset, limit)
        if view == 'metadata':
            if index is None:
                return page([{'index': i, 'kind': m.kind, 'offset': m.offset, 'name': m.name,
                              'buffer_size': m.buffer_size, 'member_count': len(m.members)}
                             for i, m in enumerate(self.metadata)], offset, limit)
            if not 0 <= index < len(self.metadata):
                raise ValueError('Metadata 索引超出范围')
            row = public(self.metadata[index])
            row['members'] = page(row['members'], offset, limit)
            return row
        if view != 'summary':
            raise ValueError('未知结构报告类别')
        counts = Counter(d.severity for d in self.diagnostics)
        return {'sha256': self.sha256, 'header': public(self.header),
                'program_count': len(self.programs), 'shader_count': len(self.shader_users),
                'object_count': len(self.objects),
                'metadata_count': len(self.metadata),
                'unmodeled_range_count': len(self.unmodeled_ranges),
                'state_cache_key_algorithm': 'opaque',
                'shared_object_count': sum(len(o.users) > 1 for o in self.objects),
                'diagnostic_counts': dict(counts),
                'preflight': {'structure': 'failed' if counts['error'] else 'warning' if counts['warning'] else 'passed',
                              'shader_analysis': 'not_run', 'auto_extension': 'unproven',
                              'state_editing': 'unsupported',
                              'reason': '只读结构检查；需关联 MDF、完整寻址及隔离 DXC 验证才能批准扩展。'},
                'diagnostics': page([public(x) for x in self.diagnostics], offset, limit)}


# 字节以 hex 返回，避免 JSON 编码错误；快照内部保留 bytes 方便逐字节核对。
def public(value):
    def convert(row):
        if isinstance(row, bytes):
            return row.hex()
        if isinstance(row, dict):
            return {k: convert(v) for k, v in row.items()}
        if isinstance(row, (tuple, list)):
            return [convert(v) for v in row]
        return row
    return convert(asdict(value))


def page(rows, offset, limit):
    return {'total': len(rows), 'offset': offset, 'items': rows[offset:offset+limit],
            'next_offset': offset+limit if offset+limit < len(rows) else None}


# 解析器只拥有本次不可变候选；所有新增约束报告诊断，不向 Document 提交任何修改。
def parse_structure(data, path, info=None):
    data = bytes(data)
    identity = hashlib.sha256(data).hexdigest()
    if info is None:
        _, info = inspect_data(data, Path(path))
    elif info.get('sha256') != identity:
        raise ValueError('兼容解析快照与当前字节 SHA256 不同')
    diagnostics, programs = [], []
    objects, users = {}, defaultdict(list)
    shader_users = [[] for _ in info['shaders']]
    lower, upper = info['record_end'], info['shader_start']
    version = info['resource_version']
    record_size = info['record_size']

    def diagnostic(code, program, field, message, severity='error'):
        diagnostics.append(Diagnostic(severity, code, program, field, message))

    if version not in KNOWN_VERSIONS:
        diagnostic('resource_version_unverified', None, 0, f'资源版本未验证: {version}', 'warning')
    if info['header_u16_04'] * info['header_u16_06'] != len(info['records']):
        diagnostic('header_count', None, 4, 'header 声明数量与兼容解析结果不同')

    def span(kind, pointer, size, use, values=()):
        if not pointer or size <= 0 or pointer < lower or pointer + size > upper:
            diagnostic('object_range', use.program, use.field_offset,
                       f'{kind} 范围 {pointer:#x}+{size:#x} 不在 metadata 区')
            return None
        key = f'{kind}:{pointer:x}:{size:x}'
        objects.setdefault(key, (kind, pointer, size, data[pointer:pointer+size], values))
        users[key].append(use)
        return key

    metadata_by_offset = {kind: {e['offset']: e for e in entries}
                          for kind, entries in info['metadata'].items()}
    for record in info['records']:
        i, base = record['index'], record['offset']
        raw = data[base:base+record_size]
        refs, tables, object_refs = [], [], []
        for ref in record['shader_references']:
            field = ref['field']
            lane = 'input_signature' if field == 8 else STAGES[(field-0x10)//8]
            refs.append(ShaderRef(base+field, lane, ref['shader_index'], ref['offset'], ref['size'], ref['stage']))
            shader_users[ref['shader_index']].append(PointerUse(i, base+field, lane))
            # 荒野文件在 hull legacy lane 中复用 mesh Shader；保留 lane 和实际阶段两个身份。
            if field != 8 and ref['stage'] not in (lane, 'ms' if lane == 'hs' else lane):
                diagnostic('shader_stage', i, base+field,
                           f'legacy lane {lane} 指向实际 {ref["stage"]}', 'warning')
        for kind_index, kind in enumerate(TABLES):
            count = raw[COUNTS[kind_index]]
            bp, mp = struct.unpack_from('<QQ', raw, 0x68+kind_index*16)
            expected = tuple(raw[0xD8+kind_index*6:0xDE+kind_index*6])
            descriptors = []
            bkey = mkey = None
            if count:
                bkey = span(f'{kind}_bindings', bp, count*8, PointerUse(i, base+0x68+kind_index*16, kind))
                width = 32 if kind == 'cbuffers' else 16
                mkey = span(f'{kind}_metadata', mp, count*width, PointerUse(i, base+0x70+kind_index*16, kind))
                if kind == 'optional':
                    diagnostic('optional_resource_unverified', i, base+0xA8,
                               '第五类资源尚无非空真实语料，metadata 宽度为候选解释', 'warning')
                for j in range(count) if bkey else ():
                    address = bp+j*8
                    blob = data[address:address+8]
                    entry = metadata_by_offset.get(kind, {}).get(mp+j*width)
                    descriptors.append(ResourceBinding(kind, j, address, mp+j*width,
                        entry['name'] if entry else None, tuple(blob[:6]), blob[6], blob[7], blob))
                    if blob[6] & ~63:
                        diagnostic('binding_mask', i, address+6, f'未知阶段 mask {blob[6]:#x}')
                    if blob[7] not in TYPES[kind_index]:
                        diagnostic('binding_type_unknown', i, address+7,
                                   f'{kind} 类型 {blob[7]:#x} 未验证', 'warning')
                if mkey and kind == 'cbuffers':
                    for j in range(count):
                        entry = metadata_by_offset[kind].get(mp+j*32)
                        if entry and entry['member_count']:
                            key = span('cbuffer_members', entry['members_offset'], entry['member_count']*16,
                                       PointerUse(i, mp+j*32+24, 'cbuffer_members'))
                            if key:
                                object_refs.append(key)
            elif bp or mp:
                diagnostic('empty_table_pointer', i, base+0x68+kind_index*16,
                           '零计数仍有表指针；保留原值', 'warning')
            actual = tuple(sum(bool(b.stage_mask & (1 << stage)) for b in descriptors) for stage in range(6))
            if actual != expected:
                diagnostic('binding_stage_counts', i, base+0xD8+kind_index*6,
                           f'{kind} mask 计数 {actual} != 声明 {expected}')
            tables.append(TableRef(kind, bkey, mkey, count, expected, tuple(descriptors)))
            object_refs.extend(k for k in (bkey, mkey) if k)
        total = struct.unpack_from('<I', raw, 0xD4)[0]
        if total != sum(sum(t.expected_counts) for t in tables):
            diagnostic('binding_total', i, base+0xD4, '全阶段总数与分阶段计数不符')
        for kind, field, size in [('input_layout', 0x40, 8), ('rasterizer', 0x48, 16),
                                  ('blend', 0x50, 36), ('depth_stencil', 0x58, 8),
                                  ('compact_declaration', 0x60, raw[0xF8]*4)]:
            pointer = struct.unpack_from('<Q', raw, field)[0]
            if not pointer:
                if kind == 'compact_declaration' and size:
                    diagnostic('compact_pointer', i, base+field, 'compact count 非零但指针为空')
                continue
            values = ()
            if kind == 'input_layout' and lower <= pointer <= upper-8:
                count, reserved = struct.unpack_from('<II', data, pointer)
                size = 8+count*8
                if size <= upper-pointer:
                    elements = tuple(struct.unpack_from('<4BI', data, pointer+8+j*8) for j in range(count))
                    values = (('count', count), ('reserved', reserved), ('elements_raw', elements))
                if reserved:
                    diagnostic('input_reserved', i, pointer+4, 'input layout reserved 非零', 'warning')
            elif kind == 'rasterizer' and lower <= pointer <= upper-size:
                flags = struct.unpack_from('<I', data, pointer)[0]
                values = (('flags_raw', flags), ('fill_raw', (flags >> 5) & 3),
                          ('cull_raw', (flags >> 7) & 3),
                          ('depth_bias', struct.unpack_from('<i', data, pointer+4)[0]))
            elif kind == 'blend' and lower <= pointer <= upper-size:
                packed = struct.unpack_from('<9I', data, pointer)
                values = (('targets_raw', packed[:8]), ('flags_raw', packed[8]))
            elif kind == 'depth_stencil' and lower <= pointer <= upper-size:
                flags = struct.unpack_from('<Q', data, pointer)[0]
                values = (('packed_raw', flags), ('depth_enable', bool(flags & 1)),
                          ('stencil_enable', bool(flags & 128)), ('reserved_raw', flags >> 57))
            key = span(kind, pointer, size, PointerUse(i, base+field, kind), values)
            if key:
                object_refs.append(key)
                if kind in ('input_layout', 'rasterizer', 'blend', 'depth_stencil'):
                    trailer_key = span(f'{kind}_cache_key', pointer+size, 8,
                                       PointerUse(i, base+field, f'{kind}_cache_key'),
                                       (('algorithm', 'opaque'),))
                    if trailer_key:
                        object_refs.append(trailer_key)
        programs.append(Program(i, i//max(1, info['header_u16_06']), base, record['name'], raw,
            tuple(refs), tuple(tables), tuple(object_refs), total,
            struct.unpack_from('<3H', raw, record_size-8), struct.unpack_from('<H', raw, record_size-2)[0], raw[0xFF:record_size-8].hex()))

    # 相同范围异类、同址异长和部分交叠分别报出；同类完全共享只记录反向使用者。
    ordered = sorted(objects.items(), key=lambda pair: (pair[1][1], pair[1][2], pair[0]))
    active = []
    for key, obj in ordered:
        active = [(k, o) for k, o in active if o[1]+o[2] > obj[1]]
        for other_key, other in active:
            # metadata 表切片共享可以成立，仍需让调用方知道并避免局部原地改写。
            use = users[key][0]
            diagnostic('object_overlap', use.program, use.field_offset,
                       f'{other_key} 与 {key} 的范围重叠', 'warning')
        active.append((key, obj))
    raw_objects = tuple(RawObject(key, *obj, tuple(users[key])) for key, obj in ordered)
    metadata = tuple(Metadata(kind, e['offset'], e['name'], e['name_offset'], e['name_hash_raw'],
        e['reserved_raw'], e.get('buffer_size'), e.get('members_offset'),
        tuple(CbufferMember(m['offset'], m['name'], m['name_offset'], m['name_hash_raw'],
                            m['byte_offset'], m['byte_size'], data[m['offset']:m['offset']+16])
              for m in e.get('members', ())), data[e['offset']:e['offset']+(32 if kind == 'cbuffers' else 16)])
        for kind, entries in info['metadata'].items() for e in entries)
    # 未解释的前缀范围包括字符串、padding 和其它未知字段；只是覆盖清单，不推断其语义。
    unmodeled, cursor = [], lower
    for obj in raw_objects:
        if obj.offset > cursor:
            unmodeled.append((cursor, obj.offset-cursor))
        cursor = max(cursor, obj.offset+obj.size)
    if cursor < upper:
        unmodeled.append((cursor, upper-cursor))
    header = Header(data[:info['header_size']], info['header_u16_04'], info['header_u16_06'], upper, lower, version)
    return Structure(identity, data, header, tuple(programs), raw_objects, metadata,
                     tuple(tuple(rows) for rows in shader_users), tuple(diagnostics), tuple(unmodeled))


# 调用时才构建当前候选，缓存按完整字节身份；不把原文件索引用于已重定位的编辑结果。
def document_structure(document):
    data = document.build()[0]
    identity = hashlib.sha256(data).hexdigest()
    cached = getattr(document, '_structure_cache', None)
    if cached is None or cached.sha256 != identity:
        info = document.info if data == document.original else None
        cached = parse_structure(data, document.source, info)
        document._structure_cache = cached
    return cached
