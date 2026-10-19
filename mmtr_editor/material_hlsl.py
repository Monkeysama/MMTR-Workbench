"""Bounded PS/VS/CS reconstruction with reflected layouts and descriptor heaps.

This translator supports one reducible natural loop and rejects unknown operations.
It emits structured branches, parallel phi-edge assignments and loop state copies,
duplicating only shared scalar comparison/discard tails.
"""
from dataclasses import dataclass
import re
import struct
from inspect_mmtr import reflection
from .reconstruct import ReconstructionError, shader_interface, interface_declarations, entry_attributes
from .instruction_metadata import precise_instruction


def split_operands(text):
    result, start, depth = [], 0, 0
    for i, ch in enumerate(text):
        if ch in '([{<':
            depth += 1
        elif ch in ')]}>':
            depth -= 1
        elif ch == ',' and depth == 0:
            result.append(text[start:i].strip())
            start = i + 1
    result.append(text[start:].strip())
    return result


def strip_type(value):
    m = re.fullmatch(r'(?:float|half|i\d+|%[\w.]+)\s+(.+)', value)
    if not m:
        raise ReconstructionError('不支持的操作数类型：' + value)
    return m[1]


@dataclass
class Value:
    type: str
    expr: str


class MaterialTranslator:
    def __init__(self, ir):
        self.ir = ir
        self.entry, body, self.profile, self.inputs, self.outputs = shader_interface(ir)
        metadata = dict(re.findall(r'^!(\d+) = !\{(.*)\}$',ir,re.M))
        entry_root = re.search(r'^!dx.entryPoints = !\{!(\d+)\}',ir,re.M)
        property_ref = re.search(r', !(\d+)$',metadata[entry_root[1]])
        native_flags = re.search(r'i32 0, i64 (\d+)',metadata[property_ref[1]]) if property_ref else None
        self.native_half = bool(native_flags and int(native_flags[1]) & (1 << 23)) and bool(re.search(r'\bhalf\b',body))
        self.lines = interface_declarations(self.inputs, self.outputs,self.profile[:2])
        from .source_map import IRSourceIndex
        self.source_index=IRSourceIndex(ir)
        self.line_origins={};self.origin_context=()
        self.mesh_pending_origins=[]
        self.mesh=None
        self.mesh_pending_output=None
        if self.profile.startswith('ms_'):
            from .mesh import mesh_interface
            self.mesh=mesh_interface(ir)
            self.lines.extend(line.replace('ShaderOutput','MeshPrimitive') for line in interface_declarations({},self.mesh['primitives'],'ms')[2:])
        self.values, self.handles, self.aggregates, self.pointers, self.constants = {}, {}, {}, {}, {}
        self.bindings, self.buffers = {}, {}
        self.extra_types = {}
        self.instructions = 0
        self.precise_results = set()
        self.blocks = {'0': []}
        current = '0'
        for raw in body.splitlines():
            label = re.match(r'(?:; <label>:(\d+)|([\w.]+):)', raw.strip())
            if label:
                current = label[1] or label[2]
                if current in self.blocks:
                    raise ReconstructionError('重复基本块标签。')
                self.blocks[current] = []
            else:
                line = raw.split(';', 1)[0].strip()
                # Branch/flatten hints influence optimization, not the branch predicate.
                line = re.sub(r', !dx\.controlflow\.hints !\d+$', '', line)
                line, precise_result = precise_instruction(line, metadata)
                if precise_result:
                    self.precise_results.add(precise_result)
                if line:
                    self.blocks[current].append(line)
                    self.instructions += 1
        self.successors = {}
        self.phis = {}
        self.duplicated_scalar_blocks = set()
        self.duplicated_reporting_blocks = set()
        self.shared_region_stack=[]
        self.structured_shared_blocks=set()
        for label, instructions in self.blocks.items():
            if not instructions:
                raise ReconstructionError('空基本块。')
            term = instructions[-1]
            self.successors[label] = re.findall(r'label %([\w.]+)', term)
            if term != 'ret void' and not term.startswith('br '):
                raise ReconstructionError('不支持的控制流终结：' + term)
            for line in instructions:
                if ' = phi ' in line:
                    m = re.fullmatch(r'(%\d+) = phi (float|half|i32|i64|i1) (.+)', line)
                    if not m:
                        raise ReconstructionError('不支持的 phi 类型。')
                    edges = re.findall(r'\[ ([^,]+), %([\w.]+) \]', m[3])
                    if not edges or len(edges) != len(split_operands(m[3])):
                        raise ReconstructionError('无法解析 phi 输入。')
                    self.phis[m[1]] = (label, {'float':'float','half':'min16float','i32':'uint','i64':'uint64_t','i1':'bool'}[m[2]], dict((b, v) for v, b in edges))
        self.validate_cfg()
        from .local_array import analyze_local_arrays
        self.local_arrays, self.local_pointers, self.local_loads, self.local_stores, ignored = analyze_local_arrays(self.blocks)
        self.local_ignored_lines = {self.blocks[b][p] for b,p in ignored}
        if self.local_arrays and self.loop:
            raise ReconstructionError('局部数组与循环组合尚未验证。')
        self.declare_resources()
        from .shared_memory import analyze_shared_memory
        self.shared_arrays,self.shared_pointers,self.shared_loads,self.shared_stores,self.shared_barriers=analyze_shared_memory(ir,self.blocks)
        if self.shared_arrays and not self.profile.startswith('cs_'):
            raise ReconstructionError('groupshared源码重建仅支持CS。')
        for array in self.shared_arrays.values():
            self.lines.append(f'groupshared float {array["name"]}[{array["size"]}];')
        # Prevent new floating-point reassociation while reconstructing sampled
        # materials. Scalar depth-only fixtures retain their exact legacy form.
        self.precise = bool(self.precise_results) or self.profile.startswith(('vs_','cs_','ms_')) or any(r['kind'] in ('sampler', 'bytebuffer', 'structured', 'rwstructured') for r in self.bindings.values())
        # Minimum16 saturation/max are emitted at float width, then rounded
        # back to half. This keeps precise legal without removing half bounds.
        definitions = dict(m.groups() for lines in self.blocks.values() for line in lines
                           if (m := re.fullmatch(r'(%\d+) = (.+)',line)))
        self.scalar_definitions = definitions
        for m in re.finditer(r'^@(\w+) = internal unnamed_addr constant \[(\d+) x float\] \[(.+)\], align 4$', ir, re.M):
            values = split_operands(m[3])
            if len(values) != int(m[2]) or any(not x.startswith('float ') for x in values):
                raise ReconstructionError('不完整的静态常量数组。')
            self.constants[m[1]] = int(m[2])
            self.lines.append(f'static const float {m[1]}[{m[2]}] = {{' + ', '.join(self.operand(x[6:]) for x in values) + '};')

    def validate_cfg(self):
        from .control_flow import single_natural_loop,natural_loop_forest
        if self.profile.startswith('cs_'):
            self.loops=natural_loop_forest(self.blocks)
            self.loop=self.loops[0] if self.loops else None
        else:
            self.loop=single_natural_loop(self.blocks)
            self.loops=[self.loop] if self.loop else []
        if self.profile.startswith('ms_') and self.loop:
            raise ReconstructionError('网格源码暂不支持循环。')
        self.loop_headers={loop['header']:loop for loop in self.loops}
        self.loop_latches={loop['latch']:loop for loop in self.loops}
        self.active_loop_headers=set()
        self.emitting_loop = False
        visiting, visited = set(), set()
        predecessors = {key: set() for key in self.blocks}
        for label, children in self.successors.items():
            for child in children:
                if child not in self.blocks:
                    raise ReconstructionError('未知分支目标。')
                predecessors[child].add(label)

        def visit(label):
            if label in visiting:
                raise ReconstructionError('当前重建不支持循环控制流。')
            if label in visited:
                return
            visiting.add(label)
            for child in self.successors[label]:
                if label in self.loop_latches and child==self.loop_latches[label]['header']:
                    continue
                visit(child)
            visiting.remove(label)
            visited.add(label)
        visit('0')
        if visited != set(self.blocks):
            raise ReconstructionError('存在不可达块。')
        for label, _, edges in self.phis.values():
            if set(edges) != predecessors[label]:
                raise ReconstructionError('phi 前驱与控制流不一致。')
        # A virtual common exit makes postdominator analysis valid for both return arms.
        self.postdom = {'exit': {'exit'}}
        all_labels = set(self.blocks) | {'exit'}
        self.postdom.update({key: set(all_labels) for key in self.blocks})
        changed = True
        while changed:
            changed = False
            for key in reversed(list(self.blocks)):
                succ = ([self.loop_latches[key]['exit']] if key in self.loop_latches
                        else self.successors[key]) or ['exit']
                value = {key} | set.intersection(*(self.postdom[x] for x in succ))
                if value != self.postdom[key]:
                    self.postdom[key] = value
                    changed = True

    def declare_resources(self):
        ref = reflection(self.ir)
        nodes = dict(re.findall(r'^!(\d+) = !\{(.*)\}$', self.ir, re.M))
        root = re.search(r'^!dx.resources = !\{!(\d+)\}', self.ir, re.M)
        resource_nodes = {}
        if root:
            for cls, side in zip((0, 1, 2, 3), nodes[root[1]].split(', ')):
                if side != 'null':
                    for index in re.findall(r'!(\d+)', nodes[side[1:]]):
                        rid = re.match(r'i32 (\d+),', nodes[index])
                        resource_nodes[(cls, int(rid[1]))] = nodes[index]
        for row in ref['resource_binding_rows']:
            # DXC's fixed-width table joins the space and count columns for
            # unbounded ranges (e.g. t0,space1unbounded).
            binding_row = re.sub(r'(?<=\d)unbounded$', ' unbounded', row)
            m = re.fullmatch(r'(\w+)\s+(cbuffer|texture|sampler|UAV)\s+(\w+)\s+([\w/]+)\s+(CB|T|S|U)(\d+)\s+(cb|t|s|u)(\d+)(?:,space(\d+))?\s+(\d+|unbounded)', binding_row)
            if not m or m[10] not in ('1', 'unbounded'):
                raise ReconstructionError('当前重建不支持此资源或绑定：' + row)
            name, kind, fmt, dim = m[1], m[2], m[3], m[4]
            unbounded = m[10] == 'unbounded'
            resource = {'name': name, 'kind': kind, 'format': fmt, 'dimension': dim,
                        'register': int(m[8]), 'space': int(m[9] or 0), 'unbounded': unbounded}
            cls = {'cbuffer': 2, 'texture': 0, 'sampler': 3, 'UAV': 1}[kind]
            metadata = resource_nodes.get((cls, int(m[6])), '')
            count = '-1' if unbounded else '1'
            typed = re.fullmatch(r'i32 \d+, (.+)\* undef, !"[^"\n]*", i32 (\d+), i32 (\d+), i32 ' + count + r', (.+)', metadata)
            if not typed or int(typed[2]) != resource['space'] or int(typed[3]) != resource['register']:
                raise ReconstructionError('资源元数据与反射槽位不一致：' + name)
            if unbounded:
                array_type = re.fullmatch(r'\[0 x (%[^*]+)\]', typed[1])
                if kind != 'texture' or array_type is None or fmt not in ('byte', 'f32', 'u32', 'i32'):
                    raise ReconstructionError('尚不支持此无界资源数组类型。')
                # Keep the type validation below identical to singleton SRVs.
                metadata = re.sub(re.escape(typed[1]), lambda _: array_type[1], metadata, count=1)
                typed = re.fullmatch(r'i32 \d+, (%[^*]+)\* undef, !"[^"\n]*", i32 (\d+), i32 (\d+), i32 -1, (.+)', metadata)
            self.bindings[(cls, int(m[6]))] = resource
            register = {'cbuffer': 'b', 'texture': 't', 'sampler': 's', 'UAV': 'u'}[kind] + m[8]
            space = ', space' + m[9] if m[9] else ''
            declared_name = name + ('[]' if unbounded else '')
            if kind == 'UAV':
                if self.profile.startswith('cs_') and fmt=='u32' and dim=='2d' and typed[1]=='%"class.RWTexture2D<unsigned int>"':
                    props=re.fullmatch(r'i32 2, i1 false, i1 false, i1 false, !(\d+)',typed[4])
                    if not props or nodes.get(props[1])!='i32 0, i32 5':
                        raise ReconstructionError('RWTexture2D<uint>属性不匹配。')
                    resource.update(kind='rwtexture2d',base='uint',width=1)
                    self.lines.append(f'RWTexture2D<uint> {name} : register({register}{space});')
                    continue
                if fmt == 'struct' and dim == 'r/w':
                    self.declare_structured(resource, typed, nodes, register, space, writable=True)
                    continue
                if typed[1] != '%struct.RWByteAddressBuffer' or fmt != 'byte' or dim != 'r/w' or typed[4] != 'i32 11, i1 false, i1 false, i1 false, null':
                    raise ReconstructionError('尚不支持此 UAV 类型、计数器或一致性属性。')
                resource['kind'] = 'rwbytebuffer'
                self.lines.append(f'RWByteAddressBuffer {name} : register({register}{space});')
                continue
            if kind == 'sampler':
                if typed[1] != '%struct.SamplerState' or not typed[4].startswith('i32 0,'):
                    raise ReconstructionError('尚不支持比较采样器。')
                self.lines.append(f'SamplerState {name} : register({register}{space});')
                continue
            if kind == 'texture':
                if fmt == 'struct' and dim == 'r/o':
                    self.declare_structured(resource, typed, nodes, register, space)
                    continue
                if typed[1] == '%struct.ByteAddressBuffer' and fmt == 'byte' and dim == 'r/o' and typed[4].startswith('i32 11,'):
                    resource['kind'] = 'bytebuffer'
                    self.lines.append(f'ByteAddressBuffer {declared_name} : register({register}{space});')
                    continue
                texture = re.fullmatch(r'%"class\.(Texture2DArray|Texture2D|Texture3D)<(?:(float|uint|int|unsigned int)|vector<(float|uint|int|unsigned int), ([1-4])> )>"', typed[1])
                if not texture:
                    raise ReconstructionError('尚不支持此纹理类型：' + typed[1])
                tex_kind, base, width = texture[1], texture[2] or texture[3], int(texture[4] or 1)
                base = 'uint' if base == 'unsigned int' else base
                expected_dim, dx_kind, coords, gradients = {'Texture2D': ('2d', 2, 2, 2),
                    'Texture2DArray': ('2darray', 7, 3, 2), 'Texture3D': ('3d', 4, 3, 3)}[tex_kind]
                if dim != expected_dim or fmt != {'float': 'f32', 'uint': 'u32', 'int': 'i32'}[base] or not typed[4].startswith(f'i32 {dx_kind},'):
                    raise ReconstructionError('纹理类型与反射维度不一致：' + name)
                resource.update({'base': base, 'width': width, 'coords': coords, 'gradients': gradients})
                self.lines.append(f'{tex_kind}<{base}{width if width > 1 else ""}> {declared_name} : register({register}{space});')
                continue
            cb = next((x for x in ref['cbuffers'] if x['name'] == name), None)
            if cb is None or not cb['variables']:
                raise ReconstructionError('缺少常量缓冲成员反射。')
            resource['size'] = cb['size']
            # Struct member declarations retain compiler reflection type spelling.
            self.lines.append(f'cbuffer {name} : register({register}{space}) {{')
            slots = {}
            for var in cb['variables']:
                t = re.fullmatch(r'(?:(row_major|column_major) )?(float|uint|int)([1-4])?(?:x([1-4]))?', var['type'])
                n = re.fullmatch(r'(\w+)(?:\[(\d+)\])?', var['name'])
                if not t or not n or var['offset'] % 4:
                    raise ReconstructionError('不支持此常量缓冲字段：' + str(var))
                base, width, columns = t[2], int(t[3] or 1), int(t[4] or 0)
                count, is_array = int(n[2] or 1), n[2] is not None
                reg, lane = divmod(var['offset'] // 4, 4)
                if (columns or is_array) and lane:
                    raise ReconstructionError('数组/矩阵不是寄存器对齐。')
                offset = f'c{reg}' + ('.' + 'xyzw'[lane] if lane else '')
                self.lines.append(f'    {var["type"]} {var["name"]} : packoffset({offset}); // byte {var["offset"]}')
                for a in range(count):
                    suffix = f'[{a}]' if is_array else ''
                    if columns:
                        major = t[1] or 'column_major'
                        rows_per = width if major == 'row_major' else columns
                        for row in range(width):
                            for col in range(columns):
                                slot = reg * 4 + a * rows_per * 4 + (row * 4 + col if major == 'row_major' else col * 4 + row)
                                if slot in slots:
                                    raise ReconstructionError('常量布局重叠。')
                                slots[slot] = Value(base, f'{n[1]}{suffix}[{row}][{col}]')
                    else:
                        for c in range(width):
                            slot = reg * 4 + lane + (a * 4 if is_array else 0) + c
                            if slot in slots:
                                raise ReconstructionError('常量布局重叠。')
                            slots[slot] = Value(base, n[1] + suffix + ('.' + 'xyzw'[c] if width > 1 else ''))
            if cb['size'] is None or any(slot * 4 + 4 > cb['size'] for slot in slots):
                raise ReconstructionError('常量字段超出反射总长度。')
            self.lines.append('};')
            self.buffers[name] = slots

    def declare_structured(self, resource, typed, nodes, register, space, writable=False):
        """Recover flat, tightly packed 32-bit structs, checking both metadata and LLVM types."""
        buffer_type = 'RWStructuredBuffer' if writable else 'StructuredBuffer'
        match = re.fullmatch(r'%"(?:hostlayout\.)?class\.' + buffer_type + r'<(.+)>"', typed[1])
        properties = re.fullmatch(r'i32 12, ' + (r'i1 false, i1 false, i1 false, ' if writable else r'i32 0, ') + r'!(\d+)', typed[4])
        stride = re.fullmatch(r'i32 1, i32 (\d+)(?:, i32 3, i32 1)?', nodes.get(properties[1], '')) if properties else None
        reflected = next((cb for cb in reflection(self.ir)['structured_buffers'] if cb['name'] == resource['name']), None)
        if not match or not stride or not reflected or reflected['size'] != int(stride[1]):
            raise ReconstructionError('结构化缓冲布局或步长不完整：' + resource['name'])
        if 'element_type' in reflected:
            if self.profile.startswith('cs_') and writable and reflected['element_type']=='uint64_t':
                definition=re.search(r'^'+re.escape(typed[1])+r' = type \{ (.+) \}$',self.ir,re.M)
                if match[1]!='unsigned long long' or int(stride[1])!=8 or not definition or definition[1]!='i64':
                    raise ReconstructionError('uint64结构化元素LLVM类型或步长不一致。')
                resource.update(kind='rwstructured64',stride=8,slots={0:Value('uint64_t','')})
                self.lines.append(f'RWStructuredBuffer<uint64_t> {resource["name"]} : register({register}{space});')
                return
            element=re.fullmatch(r'(uint|int|float)([1-4])?',reflected['element_type'])
            base=element[1] if element else None
            width=int(element[2] or 1) if element else 0
            llvm_type = {'uint': 'i32', 'int': 'i32', 'float': 'float'}.get(base)
            llvm_name = {'uint': 'unsigned int', 'int': 'int', 'float': 'float'}.get(base)
            if width>1:
                llvm_type=f'<{width} x {llvm_type}>'
                llvm_name=f'vector<{llvm_name}, {width}> '
            definition = re.search(r'^' + re.escape(typed[1]) + r' = type \{ (.+) \}$', self.ir, re.M)
            if base is None or match[1] != llvm_name or reflected['size'] != width*4 or not definition or definition[1] != llvm_type:
                raise ReconstructionError('不支持的结构化标量元素。')
            self.lines.append(f'{buffer_type}<{reflected["element_type"]}> {resource["name"]} : register({register}{space});')
            resource.update({'kind': 'rwstructured' if writable else 'structured', 'stride': width*4,
                             'slots': {i:Value(base,'xyzw'[i]) for i in range(width)} if width>1 else {0:Value(base,'')}})
            return
        if not reflected['variables'] or not re.fullmatch(r'\w+', match[1]):
            raise ReconstructionError('结构化缓冲字段或类型名称不完整。')
        container = re.search(r'^' + re.escape(typed[1]) + r' = type \{ (%[\w.]+) \}$', self.ir, re.M)
        definition = re.search(r'^' + re.escape(container[1]) + r' = type \{ (.+) \}$', self.ir, re.M) if container else None
        if 'members' in reflected:
            from .structured_layout import nested_struct_layout
            if not container:raise ReconstructionError('嵌套结构缺少LLVM根类型。')
            declarations,slots=nested_struct_layout(self.ir,match[1],container[1],reflected['members'],int(stride[1]))
            for declaration in declarations:
                name=declaration.split(' ',2)[1]
                existing=next((line for line in self.lines if line.startswith(f'struct {name} {{')),None)
                if existing is not None and existing!=declaration:raise ReconstructionError('同名结构化类型布局不一致。')
                if existing is None:self.lines.append(declaration)
            self.lines.append(f'{buffer_type}<{match[1]}> {resource["name"]} : register({register}{space});')
            resource.update({'kind':'rwstructured' if writable else 'structured','stride':int(stride[1]),'slots':slots})
            return
        expected_fields, slots, declarations = [], {}, []
        for var in reflected['variables']:
            t = re.fullmatch(r'(?:(row_major|column_major) )?(float|uint|int)([1-4])?(?:x([1-4]))?', var['type'])
            n = re.fullmatch(r'(\w+)(?:\[(\d+)\])?', var['name'])
            if not t or not n or var['offset'] % 4:
                raise ReconstructionError('不支持的结构化缓冲字段：' + str(var))
            base, width, columns = t[2], int(t[3] or 1), int(t[4] or 0)
            count, is_array = int(n[2] or 1), n[2] is not None
            scalar = 'float' if base == 'float' else 'i32'
            llvm = scalar if width == 1 else f'<{width} x {scalar}>'
            if columns:
                rows = width if t[1] == 'row_major' else columns
                vector = columns if t[1] == 'row_major' else width
                llvm = f'[{rows} x <{vector} x {scalar}>]'
            if is_array:
                llvm = f'[{count} x {llvm}]'
            expected_fields.append(llvm)
            declarations.append(f'    {var["type"]} {var["name"]};')
            for a in range(count):
                suffix = f'[{a}]' if is_array else ''
                for row in range(width):
                    for col in range(columns or 1):
                        offset = (row * columns + col if t[1] == 'row_major' else col * width + row) if columns else row
                        slot = var['offset']//4 + a * width * (columns or 1) + offset
                        if slot in slots:
                            raise ReconstructionError('结构化缓冲字段重叠。')
                        access = f'{n[1]}{suffix}' + (f'[{row}][{col}]' if columns else ('.'+'xyzw'[row] if width > 1 else ''))
                        slots[slot] = Value(base, access)
        actual_fields = split_operands(definition[1]) if definition else []
        for index, field in enumerate(actual_fields):
            if field.startswith('%class.matrix.'):
                matrix = re.search(r'^' + re.escape(field) + r' = type \{ (.+) \}$', self.ir, re.M)
                if matrix:
                    actual_fields[index] = matrix[1]
        if definition is None or actual_fields != expected_fields:
            raise ReconstructionError('结构化缓冲字段反射与 LLVM 类型不一致。')
        if set(slots) != set(range(reflected['size']//4)) or reflected['size'] % 4:
            raise ReconstructionError('结构化缓冲存在填充或未知布局。')
        declaration = f'struct {match[1]} {{\n' + '\n'.join(declarations) + '\n};'
        # Multiple bindings may share the same struct, but incompatible definitions must fail.
        existing = next((line for line in self.lines if line.startswith(f'struct {match[1]} {{')), None)
        if existing is not None and existing != declaration:
            raise ReconstructionError('同名结构化类型布局不一致。')
        if existing is None:
            self.lines.append(declaration)
        self.lines.append(f'{buffer_type}<{match[1]}> {resource["name"]} : register({register}{space});')
        resource.update({'kind': 'rwstructured' if writable else 'structured', 'stride': int(stride[1]), 'slots': slots})

    # 单次重建独占资源表；按真实注解声明heap纹理，维度用于约束坐标/梯度/offset，不补造分量。
    def declare_heap_resource(self, dest, source, kind, properties, level):
        """Use the DXIL annotation's actual heap type; unknown descriptors fail."""
        variable = 'heap_' + dest[1:]
        index = self.operand(source['index'], 'uint')
        if source['nonuniform']:
            index = f'NonUniformResourceIndex({index})'
        if kind == 13:
            if not 0 < properties <= 65536 or properties % 16:
                raise ReconstructionError('描述符堆常量缓冲长度不受支持。')
            typename = f'HeapRegisters{properties}'
            self.extra_types[typename] = (f'// Raw register layout: heap CBV member names are absent in DXIL.\n'
                                         f'struct {typename} {{ uint4 registers[{properties//16}]; }};')
            declaration = f'ConstantBuffer<{typename}>'
            resource = {'kind': 'heap_cbuffer', 'name': variable, 'size': properties}
        elif kind in (1, 2, 4, 7):
            width, scalar = properties >> 8, properties & 255
            base = {9: 'float', 5: 'uint', 4: 'int'}.get(scalar)
            if base is None or not 1 <= width <= 4:
                raise ReconstructionError('描述符堆纹理分量注解不支持。')
            type, dimension, coords, gradients = {1: ('Texture1D', '1d', 1, 1), 2: ('Texture2D', '2d', 2, 2),
                4: ('Texture3D', '3d', 3, 3), 7: ('Texture2DArray', '2darray', 3, 2)}[kind]
            declaration = f'{type}<{base}{width if width > 1 else ""}>'
            resource = {'kind': 'texture', 'name': variable, 'base': base, 'width': width,
                        'dimension': dimension, 'coords': coords, 'gradients': gradients}
        elif kind == 11 and properties == 0:
            declaration = 'ByteAddressBuffer'
            resource = {'kind': 'bytebuffer', 'name': variable}
        else:
            raise ReconstructionError('尚不支持此描述符堆资源注解。')
        self.emit(f'{declaration} {variable} = ResourceDescriptorHeap[{index}];', level)
        self.handles[dest] = resource

    def operand(self, token, type='float'):
        token = token.strip()
        if token.startswith('%'):
            if token not in self.values:
                raise ReconstructionError('使用未定义标量：' + token)
            value = self.values[token]
            if type in ('int', 'uint') and value.type in ('int', 'uint') and value.type != type:
                return f'{"asint" if type == "int" else "asuint"}({value.expr})'
            if type=='uint64_t' and value.type in ('uint','int'):
                return f'uint64_t({value.expr})'
            return value.expr
        if token == 'undef':
            raise ReconstructionError('未定义值不能用于实际计算。')
        if token in ('true', 'false'):
            return token
        if type == 'min16float':
            if not re.fullmatch(r'0xH[0-9A-Fa-f]{4}', token):
                raise ReconstructionError('不支持的half常量格式。')
            bits = int(token[3:], 16)
            number = struct.unpack('<e', bits.to_bytes(2, 'little'))[0]
            if number != number or abs(number) == float('inf'):
                raise ReconstructionError('half非有限常量尚不支持重建。')
            return f'min16float({repr(number)})'
        if type == 'float':
            if token.startswith('0x'):
                value = struct.unpack('>d', bytes.fromhex(token[2:]))[0]
                bits = struct.unpack('<I', struct.pack('<f', value))[0]
                return f'asfloat(0x{bits:08x}u)'
            if not re.fullmatch(r'[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?', token):
                raise ReconstructionError('不支持的浮点字面量：' + token)
            return token if '.' in token or 'e' in token.lower() else token + '.0'
        if not re.fullmatch(r'-?\d+', token):
            raise ReconstructionError('不支持的整数字面量：' + token)
        if type=='uint64_t':return str(int(token)&0xffffffffffffffff)+'ull'
        return str(int(token) & 0xffffffff) + 'u' if type == 'uint' else token

    def bind_handle(self, resource, index, nonuniform):
        if resource is None or nonuniform not in ('true', 'false'):
            raise ReconstructionError('未知资源或非一致索引标志。')
        if not resource['unbounded']:
            if nonuniform != 'false' or index != str(resource['register']):
                raise ReconstructionError('资源索引不是静态绑定槽位。')
            return resource
        # DXIL indices are absolute register indices; HLSL arrays use offsets
        # from the range's lower bound. Do not infer uniformity from the value.
        expr = self.operand(index, 'uint')
        if resource['register']:
            expr = f'({expr} - {resource["register"]}u)'
        if nonuniform == 'true':
            expr = f'NonUniformResourceIndex({expr})'
        return {**resource, 'name': f'{resource["name"]}[{expr}]'}

    def emit(self, text, level, *, origins=None):
        self.line_origins[len(self.lines)]=tuple(self.origin_context if origins is None else origins)
        self.lines.append('    ' * level + text)

    def define(self, name, type, expr, level):
        identifier = 'v_' + name[1:]
        qualifier = 'precise ' if self.precise and type in ('float','min16float') else ''
        self.emit(f'{qualifier}{type} {identifier} = {expr};', level)
        self.values[name] = Value(type, identifier)

    def component(self, signatures, key, lane, prefix):
        if key not in signatures or not 0 <= lane < signatures[key]['width']:
            raise ReconstructionError('签名通道越界。')
        s = signatures[key]
        return Value(s['type'], f'{prefix}.field_{key}' + ('.' + 'xyzw'[lane] if s['width'] > 1 else ''))

    def resource_result(self, dest, resource, expression, level, base=None, width=None):
        base, width = base or resource['base'], width or resource['width']
        identifier = 'v_' + dest[1:]
        self.emit(f'{base}{width if width > 1 else ""} {identifier} = {expression};', level)
        self.aggregates[dest] = ('vector', identifier, base, width)

    def vector_operand(self, values, base):
        parts = [self.operand(x, base) for x in values]
        return f'{base}{len(parts)}({", ".join(parts)})' if len(parts) > 1 else parts[0]

    def instruction(self, line, level, *, origin):
        previous=self.origin_context
        self.origin_context=(origin,)
        try:self._instruction(line,level)
        finally:self.origin_context=previous

    def _instruction(self, line, level):
        if self.mesh_pending_output is not None and not re.match(r'call void @dx.op.store(?:Vertex|Primitive)Output\.',line):
            raise ReconstructionError('网格向量输出需要同一基本块内连续的完整分量写入。')
        pointer_name=line.split(' = ',1)[0]
        if pointer_name in self.shared_pointers:return
        if pointer_name in self.shared_loads:
            array,index=self.shared_loads[pointer_name]
            self.define(pointer_name,'float',f'{self.shared_arrays[array]["name"]}[{self.operand(index,"uint")}]',level)
            return
        shared_store=re.fullmatch(r'store float (.+), float addrspace\(3\)\* (%[\w.]+), align 4(?:, !tbaa !\d+)?',line)
        if shared_store and shared_store[2] in self.shared_pointers:
            array,index=self.shared_pointers[shared_store[2]]
            self.emit(f'{self.shared_arrays[array]["name"]}[{self.operand(index,"uint")}] = {self.operand(shared_store[1],"float")};',level)
            return
        if line in self.local_ignored_lines:
            return
        if ' = phi ' in line:
            return
        local = re.match(r'(%[\w.]+) = ', line)
        if local and local[1] in self.local_arrays:
            self.emit(f'uint local_{local[1][1:]}[4];', level)
            return
        if local and local[1] in self.local_pointers:
            return
        if local and local[1] in self.local_loads:
            array,index = self.local_loads[local[1]]
            self.define(local[1], 'uint', f'local_{array[1:]}[{self.operand(index,"uint")}]', level)
            return
        local_store = re.fullmatch(r'store i32 (.+), i32\* (%[\w.]+), align 4(?:, !(?:tbaa|noalias|alias.scope) !\d+)*', line)
        if local_store and local_store[2] in self.local_pointers:
            array,index = self.local_pointers[local_store[2]]
            self.emit(f'local_{array[1:]}[{index}] = {self.operand(local_store[1],"uint")};', level)
            return
        call = re.fullmatch(r'(?:(%\d+) = )?call (\S+) @dx\.op\.([\w.]+)\((.*)\)', line)
        if call:
            dest, ret, name = call[1], call[2], call[3]
            args = [strip_type(x) for x in split_operands(call[4])]
            op = int(args[0])
            if name == 'createHandle' and op == 57:
                if dest is None:
                    raise ReconstructionError('资源句柄缺少结果。')
                resource = self.bindings.get((int(args[1]), int(args[2])))
                self.handles[dest] = self.bind_handle(resource, args[3], args[4])
                return
            if name == 'createHandleFromBinding' and op == 217:
                declaration = '{ i32 0, i32 0, i32 0, i8 0 }' if args[1] == 'zeroinitializer' else args[1]
                binding = re.fullmatch(r'\{ i32 (\d+), i32 (-?\d+), i32 (\d+), i8 ([0-3]) \}', declaration)
                if not binding:
                    raise ReconstructionError('无法解析资源绑定范围。')
                matches = [r for (cls, _), r in self.bindings.items() if cls == int(binding[4])
                           and r['register'] == int(binding[1]) and r['space'] == int(binding[3])
                           and (int(binding[2]) in (-1, 0xffffffff) if r['unbounded']
                                else binding[1] == binding[2])]
                if len(matches) != 1:
                    raise ReconstructionError('绑定范围与资源元数据不唯一或不一致。')
                self.handles[dest] = self.bind_handle(matches[0], args[2], args[3])
                return
            if name == 'createHandleFromHeap' and op == 218:
                if self.profile not in ('ps_6_6','vs_6_6','cs_6_6','ms_6_6') or args[2] != 'false' or args[3] not in ('true', 'false'):
                    raise ReconstructionError('尚不支持此描述符堆或 shader profile。')
                self.handles[dest] = {'kind': 'heap_untyped', 'index': args[1], 'nonuniform': args[3] == 'true'}
                return
            if name == 'annotateHandle' and op == 216:
                resource = self.handles.get(args[1])
                props = re.fullmatch(r'\{ i32 (\d+), i32 (\d+) \}', args[2])
                if resource is None or props is None:
                    raise ReconstructionError('未知资源注解。')
                kind = resource['kind']
                if kind == 'heap_untyped':
                    self.declare_heap_resource(dest, resource, int(props[1]), int(props[2]), level)
                    return
                if kind == 'cbuffer':
                    expected = (13, resource['size'])
                elif kind == 'sampler':
                    expected = (14, 0)
                elif kind == 'bytebuffer':
                    expected = (11, 0)
                elif kind == 'structured':
                    expected = (12, resource['stride'])
                    if int(props[1]) == 524:
                        expected = (524, resource['stride'])  # 4-byte SRV base alignment.
                elif kind == 'rwstructured':
                    expected = (4108, resource['stride'])
                    if int(props[1]) == 4620:
                        expected = (4620, resource['stride'])  # UAV with 4-byte base alignment.
                elif kind == 'rwbytebuffer':
                    expected = (4107, 0)
                elif kind=='rwstructured64':expected=(4108,8)
                elif kind=='rwtexture2d':expected=(4098,261)
                else:
                    expected = ({'2d': 2, '2darray': 7, '3d': 4}[resource['dimension']],
                                resource['width'] * 256 + {'float': 9, 'uint': 5, 'int': 4}[resource['base']])
                if (int(props[1]), int(props[2])) != expected:
                    raise ReconstructionError('资源注解与声明不一致。')
                self.handles[dest] = resource
                return
            if name.startswith('loadInput.') and op == 4 and args[2] == '0' and args[4] == 'undef':
                value = self.component(self.inputs, int(args[1]), int(args[3]), 'input')
                self.define(dest, value.type, value.expr, level)
                return
            if name.startswith('storeOutput.') and op == 5 and args[2] == '0':
                value = self.component(self.outputs, int(args[1]), int(args[3]), 'output')
                self.emit(f'{value.expr} = {self.operand(args[4], value.type)};', level)
                return
            if self.mesh and name.split('.')[0] in ('setMeshOutputCounts','emitIndices','storeVertexOutput','storePrimitiveOutput'):
                from .mesh import mesh_output_call
                output=mesh_output_call(line,self.mesh)
                if name=='setMeshOutputCounts':
                    self.emit(f'SetMeshOutputCounts({self.operand(args[1],"uint")}, {self.operand(args[2],"uint")});',level)
                elif name=='emitIndices':
                    self.emit(f'meshIndices[{self.operand(args[1],"uint")}] = uint3('+', '.join(self.operand(v,'uint') for v in args[2:])+');',level)
                else:
                    array='meshPrimitives' if output['primitive'] else 'meshVertices'
                    field=output['field'];lane=output['lane']
                    target=f'{array}[{self.operand(args[5],"uint")}].field_{args[1]}'
                    value=self.operand(args[4],field['type'])
                    if field['width']>1:
                        key=(array,args[5],args[1])
                        if self.mesh_pending_output is None:self.mesh_pending_output=(key,[])
                        pending_key,components=self.mesh_pending_output
                        if key!=pending_key or lane!=len(components):
                            raise ReconstructionError('网格向量输出分量重复、交叉或顺序未知。')
                        components.append(value)
                        self.mesh_pending_origins.extend(self.origin_context)
                        if len(components)<field['width']:return
                        value=field['type']+str(field['width'])+'('+', '.join(components)+')'
                        self.mesh_pending_output=None
                        self.origin_context=tuple(self.mesh_pending_origins)
                        self.mesh_pending_origins=[]
                    elif self.mesh_pending_output is not None:
                        raise ReconstructionError('网格标量输出不能插入未完成的向量分量中。')
                    self.emit(f'{target} = {value};',level)
                return
            if name.startswith('cbufferLoadLegacy.') and op == 59:
                resource = self.handles.get(args[1])
                if resource is not None and resource['kind'] == 'heap_cbuffer':
                    if not args[2].isdigit() or int(args[2])*16 >= resource['size']:
                        raise ReconstructionError('描述符堆常量缓冲读取越界或动态索引尚不支持。')
                    self.aggregates[dest] = ('heap_cbuffer', resource['name'], int(args[2]), 'float' if name.endswith('.f32') else 'uint')
                    return
                if resource is None or resource['kind'] != 'cbuffer' or not args[2].isdigit():
                    raise ReconstructionError('不支持动态常量偏移。')
                self.aggregates[dest] = ('cbuffer', resource['name'], int(args[2]), 'float' if name.endswith('.f32') else 'uint')
                return
            if name in ('textureLoad.f32', 'textureLoad.i32') and op == 66:
                resource = self.handles.get(args[1])
                if resource is None or resource['kind'] != 'texture':
                    raise ReconstructionError('不支持此纹理读取维度或 offset。')
                dims, gradient_dims = resource['coords'], resource['gradients']
                if any(x != 'undef' for x in args[3+dims:6]):
                    raise ReconstructionError('纹理 Load 多余坐标。')
                coord = self.vector_operand(args[3:3+dims] + [args[2]], 'int')
                offsets = args[6:6+gradient_dims]
                if any(x != 'undef' for x in args[6+gradient_dims:]):
                    raise ReconstructionError('纹理 Load 多余 offset。')
                suffix = '' if all(x == 'undef' for x in offsets) else ', ' + self.vector_operand(offsets, 'int')
                self.resource_result(dest, resource, f'{resource["name"]}.Load({coord}{suffix})', level)
                return
            if name in ('sample.f32', 'sampleGrad.f32', 'sampleLevel.f32') and op in (60, 62, 63):
                if len(args) != (17 if name == 'sampleGrad.f32' else 11):
                    raise ReconstructionError('采样参数数量不符。')
                resource, sampler = self.handles.get(args[1]), self.handles.get(args[2])
                if resource is None or resource['kind'] != 'texture' or resource['base'] != 'float' or sampler is None or sampler['kind'] != 'sampler':
                    raise ReconstructionError('采样资源或采样器类型不匹配。')
                dims, gradient_dims = resource['coords'], resource['gradients']
                if any(x != 'undef' for x in args[3+dims:7]) or any(x != 'undef' for x in args[7+gradient_dims:10]):
                    raise ReconstructionError('采样坐标或 offset 维度不符。')
                params = [sampler['name'], self.vector_operand(args[3:3+dims], 'float')]
                if op == 63 and name == 'sampleGrad.f32' and len(args) == 17:
                    if any(x != 'undef' for x in args[10+gradient_dims:13] + args[13+gradient_dims:16]):
                        raise ReconstructionError('梯度维度不符。')
                    params.extend(self.vector_operand(args[start:start+gradient_dims], 'float') for start in (10, 13))
                    method = 'SampleGrad'
                elif op == 60 and name == 'sample.f32':
                    # Implicit LOD uses the original quad execution context.
                    # Never replace it with SampleLevel or invented gradients.
                    method = 'Sample'
                elif op == 62 and name == 'sampleLevel.f32' and len(args) == 11:
                    params.append(self.operand(args[10]))
                    method = 'SampleLevel'
                else:
                    raise ReconstructionError('采样操作码与参数不符。')
                params.append(self.vector_operand(args[7:7+gradient_dims], 'int'))
                if op == 63 and args[16] != 'undef':
                    params.append(self.operand(args[16]))
                if op == 60 and args[10] != 'undef':
                    params.append(self.operand(args[10]))
                self.resource_result(dest, resource, f'{resource["name"]}.{method}({", ".join(params)})', level)
                return
            if name in ('rawBufferLoad.i32', 'rawBufferLoad.f32') and op == 139:
                resource = self.handles.get(args[1])
                width = {'1': 1, '3': 2, '7': 3, '15': 4}.get(args[4])
                if resource is not None and (resource['kind']=='structured' or self.profile.startswith('cs_') and resource['kind']=='rwstructured'):
                    if width is None or args[5] != '4' or not args[3].isdigit() or int(args[3]) % 4:
                        raise ReconstructionError('不支持的结构化缓冲偏移、掩码或对齐。')
                    offset = int(args[3])//4
                    base = 'float' if name.endswith('.f32') else 'uint'
                    values = []
                    for lane in range(width):
                        field = resource['slots'].get(offset + lane)
                        if field is None:
                            raise ReconstructionError('结构化缓冲读取越界。')
                        expr = f'{resource["name"]}[{self.operand(args[2], "uint")}]' + ('.' + field.expr if field.expr else '')
                        if field.type != base:
                            expr = f'{"asfloat" if base == "float" else "asuint"}({expr})'
                        values.append(Value(base, expr))
                    self.aggregates[dest] = ('structured', values)
                    return
                allowed_kind = resource is not None and (resource['kind']=='bytebuffer' or self.profile.startswith('cs_') and resource['kind']=='rwbytebuffer')
                if not allowed_kind or args[3] != 'undef' or width is None or args[5] != '4':
                    raise ReconstructionError('不支持此字节缓冲读取掩码/对齐/偏移。')
                if name != 'rawBufferLoad.i32':
                    raise ReconstructionError('字节缓冲浮点模板读取尚不支持。')
                method = 'Load' + (str(width) if width > 1 else '')
                self.resource_result(dest, resource, f'{resource["name"]}.{method}({self.operand(args[2], "uint")})', level, 'uint', width)
                return
            if name in ('rawBufferStore.f32', 'rawBufferStore.i32') and op == 140:
                if len(args) != 10:
                    raise ReconstructionError('可写结构化缓冲参数数量不符。')
                resource = self.handles.get(args[1])
                width = {'1': 1, '3': 2, '7': 3, '15': 4}.get(args[8])
                if self.profile.startswith('cs_') and resource and resource['kind']=='rwbytebuffer':
                    if name!='rawBufferStore.i32' or args[3]!='undef' or width is None or args[9]!='4' or any(v!='undef' for v in args[4+width:8]):
                        raise ReconstructionError('计算raw UAV写入仅支持连续uint通道、4字节对齐及无结构偏移。')
                    method='Store'+(str(width) if width>1 else '')
                    self.emit(f'{resource["name"]}.{method}({self.operand(args[2],"uint")}, {self.vector_operand(args[4:4+width],"uint")});',level)
                    return
                if resource is None or resource['kind'] != 'rwstructured' or width is None or args[9] != '4' or not args[3].isdigit() or int(args[3]) % 4:
                    raise ReconstructionError('不支持此结构化写入类型、掩码、偏移或对齐。')
                fields = [resource['slots'].get(int(args[3])//4 + lane) for lane in range(width)]
                base = 'float' if name.endswith('.f32') else 'uint'
                if any(f is None or (f.type == 'float') != (base == 'float') for f in fields) or any(v != 'undef' for v in args[4+width:8]):
                    raise ReconstructionError('结构化写入越界、类型或未定义通道不符。')
                if width == 1:
                    access = fields[0].expr
                else:
                    components = [re.fullmatch(r'(.+)\.([xyzw])', f.expr) for f in fields]
                    if any(m is None for m in components) or len({m[1] for m in components}) != 1:
                        raise ReconstructionError('跨字段向量写入尚不支持。')
                    access = components[0][1] + '.' + ''.join(m[2] for m in components)
                suffix = '.' + access if access else ''
                self.emit(f'{resource["name"]}[{self.operand(args[2], "uint")}]{suffix} = {self.vector_operand(args[4:4+width], fields[0].type)};', level)
                return
            if name == 'atomicBinOp.i32' and op == 78:
                resource = self.handles.get(args[1])
                methods = {'0': 'InterlockedAdd', '1': 'InterlockedAnd', '2': 'InterlockedOr', '3': 'InterlockedXor',
                           '6': 'InterlockedMin', '7': 'InterlockedMax', '8': 'InterlockedExchange'}
                if self.profile.startswith('cs_') and resource and resource['kind']=='rwtexture2d' and args[2] in methods and args[5]=='undef' and ret=='i32' and len(args)==7:
                    variable='v_'+dest[1:];self.emit(f'uint {variable};',level)
                    target=f'{resource["name"]}[uint2({self.operand(args[3],"uint")}, {self.operand(args[4],"uint")})]'
                    self.emit(f'{methods[args[2]]}({target}, {self.operand(args[6],"uint")}, {variable});',level)
                    self.values[dest]=Value('uint',variable);return
                if resource is None or resource['kind'] != 'rwbytebuffer' or args[2] not in methods or args[4:6] != ['undef', 'undef']:
                    raise ReconstructionError('尚不支持此原子操作或 UAV 偏移形式。')
                variable = 'v_' + dest[1:]
                self.emit(f'uint {variable};', level)
                self.emit(f'{resource["name"]}.{methods[args[2]]}({self.operand(args[3], "uint")}, {self.operand(args[6], "uint")}, {variable});', level)
                self.values[dest] = Value('uint', variable)
                return
            if self.profile.startswith('cs_') and name=='atomicBinOp.i64' and op==78 and ret=='i64' and len(args)==7:
                resource=self.handles.get(args[1]);typed=split_operands(call[4])
                if not resource or resource['kind']!='rwstructured64' or args[2]!='7' or args[4:6]!=['0','undef'] or typed[6].split(' ',1)[0]!='i64' or any(not x.startswith('i32 ') for x in typed[2:6]):
                    raise ReconstructionError('仅支持uint64结构化元素的InterlockedMax。')
                variable='v_'+dest[1:];self.emit(f'uint64_t {variable};',level)
                self.emit(f'InterlockedMax({resource["name"]}[{self.operand(args[3],"uint")}], {self.operand(args[6],"uint64_t")}, {variable});',level)
                self.values[dest]=Value('uint64_t',variable);return
            if self.profile.startswith('cs_') and name=='barrier' and ret=='void' and call[4]=='i32 80, i32 9':
                self.emit('GroupMemoryBarrierWithGroupSync();',level);return
            if self.profile.startswith('cs_') and name=='waveActiveBit.i32' and ret=='i32' and op==120 and len(args)==3 and args[2]=='1':
                if not re.fullmatch(r'i32 120, i32 .+, i8 1',call[4]):raise ReconstructionError('wave位或类型不符。')
                self.define(dest,'uint',f'WaveActiveBitOr({self.operand(args[1],"uint")})',level);return
            if self.profile.startswith('cs_') and name=='wavePrefixOp' and ret=='i32' and op==136 and len(args)==2:
                if not re.fullmatch(r'i32 136, i1 .+',call[4]):raise ReconstructionError('wave前缀计数类型不符。')
                self.define(dest,'uint',f'WavePrefixCountBits({self.operand(args[1],"bool")})',level);return
            if name == 'discard' and op == 82:
                self.emit(f'if ({self.operand(args[1], "bool")}) discard;', level)
                return
            if self.profile.startswith(('cs_','ms_')) and name in ('threadId.i32','groupId.i32','threadIdInGroup.i32','flattenedThreadIdInGroup.i32'):
                if self.mesh and name=='threadId.i32':raise ReconstructionError('网格暂不支持DispatchThreadID。')
                builtins={'threadId.i32':(93,'dispatchThreadID'),'groupId.i32':(94,'groupID'),
                          'threadIdInGroup.i32':(95,'groupThreadID'),'flattenedThreadIdInGroup.i32':(96,'groupIndex')}
                code,value=builtins[name]
                if ret!='i32' or op!=code or len(args)!=(1 if code==96 else 2) or code!=96 and args[1] not in ('0','1','2'):
                    raise ReconstructionError('计算线程ID操作码、类型或分量未知。')
                self.define(dest,'uint',value if code==96 else value+'.'+'xyz'[int(args[1])],level)
                return
            if name == 'waveReadLaneFirst.i32' and op == 118 and ret == 'i32' and len(args) == 2:
                self.define(dest, 'uint', f'WaveReadLaneFirst({self.operand(args[1], "uint")})', level)
                return
            if self.profile.startswith('cs_') and name=='waveAllTrue' and op==114 and ret=='i1' and len(args)==2:
                self.define(dest,'bool',f'WaveActiveAllTrue({self.operand(args[1], "bool")})',level)
                return
            if name == 'waveAllOp' and op == 135 and ret == 'i32' and len(args) == 2:
                self.define(dest, 'uint', f'WaveActiveCountBits({self.operand(args[1], "bool")})', level)
                return
            if name == 'quadOp.i32' and op == 123 and ret == 'i32' and len(args) == 3 and args[2] in ('0','1','2'):
                method = {'0':'QuadReadAcrossX','1':'QuadReadAcrossY','2':'QuadReadAcrossDiagonal'}[args[2]]
                self.define(dest, 'uint', f'{method}({self.operand(args[1], "uint")})', level)
                return
            if name == 'waveActiveOp.i32' and op == 119 and ret == 'i32' and len(args) == 4 and args[2:] == ['2','1']:
                self.define(dest, 'uint', f'WaveActiveMin({self.operand(args[1], "uint")})', level)
                return
            if name == 'sampleIndex.i32' and op == 90 and ret == 'i32' and len(args) == 1:
                sample_fields = [key for key,sig in self.inputs.items() if sig['name'] == 'SV_SampleIndex' and sig['type'] == 'uint' and sig['width'] == 1]
                if len(sample_fields) != 1:
                    raise ReconstructionError('SampleIndex需要精确的SV_SampleIndex输入。')
                self.define(dest, 'uint', f'input.field_{sample_fields[0]}', level)
                return
            if name == 'legacyF16ToF32' and op == 131 and ret == 'float' and len(args) == 2:
                self.define(dest, 'float', f'f16tof32({self.operand(args[1], "uint")})', level)
                return
            if name == 'legacyF32ToF16' and op == 130 and ret == 'i32' and len(args) == 2:
                if not re.fullmatch(r'i32 130, float .+', call[4]):
                    raise ReconstructionError('不支持此浮点打包调用类型。')
                self.define(dest, 'uint', f'f32tof16({self.operand(args[1], "float")})', level)
                return
            if name == 'isSpecialFloat.f32' and op == 10 and ret == 'i1' and len(args) == 2:
                self.define(dest, 'bool', f'isfinite({self.operand(args[1])})', level)
                return
            if name == 'unary.f16' and op == 7 and ret == 'half' and len(args) == 2:
                value = self.operand(args[1], 'min16float')
                expr = f'saturate({value})' if self.native_half else f'min16float(saturate(float({value})))'
                self.define(dest, 'min16float', expr, level)
                return
            if name == 'binary.f16' and op == 35 and ret == 'half' and len(args) == 3:
                a,b = (self.operand(arg, 'min16float') for arg in args[1:])
                if self.native_half:
                    expr = f'max({a}, {b})'
                else:
                    # A positive normal half bound makes zero/NaN ties and
                    # half-subnormal results impossible. Other forms stay gated.
                    constants = [re.fullmatch(r'0xH([0-9a-fA-F]{4})', arg) for arg in args[1:]]
                    if not any(m and 0x0400 <= int(m[1],16) < 0x7c00 for m in constants):
                        raise ReconstructionError('最小精度max提升需要正的有限正规half常量边界。')
                    expr = f'min16float(max(float({a}), float({b})))'
                self.define(dest, 'min16float', expr, level)
                return
            if name == 'binary.i32' and op == 40 and ret == 'i32' and len(args) == 3:
                self.define(dest, 'uint', f'min({self.operand(args[1], "uint")}, {self.operand(args[2], "uint")})', level)
                return
            unary = {6: 'abs', 7: 'saturate', 12: 'cos', 13: 'sin', 15: 'acos', 17: 'atan', 21: 'exp2', 22: 'frac', 23: 'log2',
                     24: 'sqrt', 25: 'rsqrt', 26: 'round', 27: 'floor', 28: 'ceil', 29: 'trunc',
                     83: 'ddx_coarse', 84: 'ddy_coarse', 85: 'ddx_fine', 86: 'ddy_fine'}
            if name == 'unary.f32' and op in unary:
                self.define(dest, 'float', f'{unary[op]}({self.operand(args[1])})', level)
                return
            binary = {35: 'max', 36: 'min'}
            if name == 'binary.f32' and op in binary:
                self.define(dest, 'float', f'{binary[op]}({self.operand(args[1])}, {self.operand(args[2])})', level)
                return
            if (name, op) in (('dot2.f32', 54), ('dot3.f32', 55), ('dot4.f32', 56)):
                width = op - 52
                a = self.vector_operand(args[1:1+width], 'float')
                b = self.vector_operand(args[1+width:1+2*width], 'float')
                self.define(dest, 'float', f'dot({a}, {b})', level)
                return
            if name == 'tertiary.f32' and op == 46:
                self.define(dest, 'float', f'mad({", ".join(self.operand(x) for x in args[1:])})', level)
                return
            raise ReconstructionError('尚不支持的 DXIL 资源或调用：' + name + f' opcode {op}')
        extract = re.fullmatch(r'(%\d+) = extractvalue %\S+ (%\d+), (\d+)', line)
        if extract:
            agg = self.aggregates.get(extract[2])
            lane = int(extract[3])
            if agg is None:
                raise ReconstructionError('未知聚合值。')
            if agg[0] == 'vector' and lane < agg[3]:
                self.values[extract[1]] = Value(agg[2], agg[1] + ('.' + 'xyzw'[lane] if agg[3] > 1 else ''))
                return
            if agg[0] == 'structured' and lane < len(agg[1]):
                value = agg[1][lane]
                self.define(extract[1], value.type, value.expr, level)
                return
            if agg[0] == 'heap_cbuffer' and lane < 4:
                expr = f'{agg[1]}.registers[{agg[2]}].{"xyzw"[lane]}'
                if agg[3] == 'float':
                    expr = f'asfloat({expr})'
                self.define(extract[1], agg[3], expr, level)
                return
            if agg[0] == 'cbuffer' and lane < 4:
                value = self.buffers[agg[1]].get(agg[2] * 4 + lane)
                if value is None:
                    raise ReconstructionError('读取常量缓冲填充或越界。')
                expression = value.expr
                if value.type != agg[3]:
                    expression = f'{"asfloat" if agg[3] == "float" else "asuint"}({expression})'
                self.define(extract[1], agg[3], expression, level)
                return
            raise ReconstructionError('不支持此聚合通道。')
        pointer = re.fullmatch(r'(%\d+) = getelementptr inbounds \[(\d+) x float\], \[\2 x float\]\* @(\w+), i32 0, i32 (.+)', line)
        if pointer:
            if self.constants.get(pointer[3]) != int(pointer[2]):
                raise ReconstructionError('未知静态数组地址。')
            self.pointers[pointer[1]] = f'{pointer[3]}[{self.operand(pointer[4], "uint")}]'
            return
        load = re.fullmatch(r'(%\d+) = load float, float\* (%\d+), align 4(?:, !(?:tbaa|noalias|alias.scope) !\d+)*', line)
        if load:
            if load[2] not in self.pointers:
                raise ReconstructionError('未知内存读取地址。')
            self.define(load[1], 'float', self.pointers[load[2]], level)
            return
        arithmetic = re.fullmatch(r'(%\d+) = (fadd|fsub|fmul|fdiv)(?: fast)? (float|half) (.+)', line)
        if arithmetic:
            type = 'float' if arithmetic[3] == 'float' else 'min16float'
            args = split_operands(arithmetic[4])
            a, b = (self.operand(x, type) for x in args)
            symbol = {'fadd': '+', 'fsub': '-', 'fmul': '*', 'fdiv': '/'}[arithmetic[2]]
            self.define(arithmetic[1], type, f'({a} {symbol} {b})', level)
            return
        integer = re.fullmatch(r'(%\d+) = (add|sub|mul|udiv|urem|and|or|xor|shl|lshr|ashr)(?: (?:nsw|nuw|exact))* (i32|i64|i1) (.+)', line)
        if integer:
            name, type = integer[2], 'bool' if integer[3] == 'i1' else ('int' if integer[2] == 'ashr' else 'uint')
            if integer[3]=='i64':
                if name not in ('or','shl'):raise ReconstructionError('仅支持i64打包or/shl。')
                type='uint64_t'
            symbols = {'add': '+', 'sub': '-', 'mul': '*', 'udiv': '/', 'urem': '%', 'and': '&', 'or': '|', 'xor': '^', 'shl': '<<', 'lshr': '>>', 'ashr': '>>'}
            if type == 'bool' and name not in ('and', 'or', 'xor'):
                raise ReconstructionError('不支持此布尔算术。')
            symbol = {'and': '&&', 'or': '||', 'xor': '!='}[name] if type == 'bool' else symbols[name]
            a, b = (self.operand(x, type) for x in split_operands(integer[4]))
            self.define(integer[1], type, f'({a} {symbol} {b})', level)
            return
        extend=re.fullmatch(r'(%\d+) = zext (i32|i1) (.+) to (i64|i32)',line)
        if extend and (extend[2],extend[4]) in (('i32','i64'),('i1','i32')):
            target='uint64_t' if extend[4]=='i64' else 'uint'
            self.define(extend[1],target,f'{target}({self.operand(extend[3],"bool" if extend[2]=="i1" else "uint")})',level);return
        select = re.fullmatch(r'(%\d+) = select i1 ([^,]+), (float|half|i32|i1) ([^,]+), \3 (.+)', line)
        if select:
            type = {'float': 'float', 'half':'min16float', 'i32': 'uint', 'i1': 'bool'}[select[3]]
            condition = self.operand(select[2], 'bool')
            a, b = self.operand(select[4], type), self.operand(select[5], type)
            self.define(select[1], type, f'({condition} ? {a} : {b})', level)
            return
        integer_cmp = re.fullmatch(r'(%\d+) = icmp (eq|ne|ugt|uge|ult|ule|sgt|sge|slt|sle) i32 (.+)', line)
        if integer_cmp:
            predicate = integer_cmp[2]
            type = 'int' if predicate.startswith('s') else 'uint'
            a, b = (self.operand(x, type) for x in split_operands(integer_cmp[3]))
            symbol = {'eq': '==', 'ne': '!=', 'gt': '>', 'ge': '>=', 'lt': '<', 'le': '<='}[predicate[-2:]]
            self.define(integer_cmp[1], 'bool', f'({a} {symbol} {b})', level)
            return
        cast = re.fullmatch(r'(%\d+) = (fptosi|fptoui|sitofp|uitofp|bitcast) (float|i32) (.+) to (float|i32)', line)
        if cast:
            name = cast[2]
            type = 'float' if cast[5] == 'float' else ('int' if name == 'fptosi' else 'uint')
            func = ('asfloat' if type == 'float' else 'asuint') if name == 'bitcast' else type
            value = self.operand(cast[4], 'float' if cast[3] == 'float' else ('int' if name == 'sitofp' else 'uint'))
            self.define(cast[1], type, f'{func}({value})', level)
            return
        half_cast = re.fullmatch(r'(%\d+) = (fptrunc float (.+) to half|fpext half (.+) to float)', line)
        if half_cast:
            type, token = ('min16float', half_cast[3]) if half_cast[3] is not None else ('float', half_cast[4])
            value = self.operand(token, 'float' if type == 'min16float' else 'min16float')
            self.define(half_cast[1], type, f'{type}({value})', level)
            return
        cmp = re.fullmatch(r'(%\d+) = fcmp(?: fast)? (ogt|oge|olt|ole|oeq|one|ugt|uge|ult|ule|ueq|une) (float|half) (.+)', line)
        if cmp:
            comparison_values = split_operands(cmp[4])
            # For ordered >0, saturate(x)>0 iff x>0, including NaN and signed
            # zero. This keeps the branch independent of a half intrinsic that
            # DXC would otherwise illegally mark precise through control use.
            if cmp[2] == 'ogt' and cmp[3] == 'half' and comparison_values[1] == '0xH0000':
                saturation = re.fullmatch(r'call half @dx.op.unary.f16\(i32 7, half ([^)]+)\)', self.scalar_definitions.get(comparison_values[0],''))
                if saturation:
                    comparison_values[0] = saturation[1]
            a, b = (self.operand(x, 'float' if cmp[3] == 'float' else 'min16float') for x in comparison_values)
            predicate = cmp[2]
            ordered = {'ogt': '>', 'oge': '>=', 'olt': '<', 'ole': '<=', 'oeq': '==', 'one': '!='}
            inverse = {'ugt': '<=', 'uge': '<', 'ult': '>=', 'ule': '>', 'ueq': '!=', 'une': '=='}
            if predicate in inverse:
                expr = f'!({a} {inverse[predicate]} {b})'
            elif predicate == 'one':
                expr = f'(!isnan({a}) && !isnan({b}) && {a} != {b})'
            else:
                expr = f'({a} {ordered[predicate]} {b})'
            self.define(cmp[1], 'bool', expr, level)
            return
        raise ReconstructionError('尚不支持的指令：' + line[:180])

    def edge(self, source, target, level):
        assignments = []
        for name, (block, type, incoming) in self.phis.items():
            if block == target:
                if source not in incoming:
                    raise ReconstructionError('phi 缺少当前前驱。')
                if incoming[source] == 'undef':
                    # Keep undefined initial phi values uninitialized. Never
                    # substitute zero or use undef in actual arithmetic.
                    self.emit(f'// v_{name[1:]} has an undefined incoming phi value.', level,
                              origins=(*self.origin_context,self.source_index.definitions[name]))
                    continue
                assignments.append((name, type, self.operand(incoming[source], type)))
        # Parallel edge copies: first evaluate all inputs, then write all phi values.
        for name, type, expr in assignments:
            self.emit(f'{type} edge_{source}_{target}_{name[1:]} = {expr};', level,
                      origins=(*self.origin_context,self.source_index.definitions[name]))
        for name, _, _ in assignments:
            self.emit(f'v_{name[1:]} = edge_{source}_{target}_{name[1:]};', level,
                      origins=(*self.origin_context,self.source_index.definitions[name]))
        for shared,join,flag in self.shared_region_stack:
            if target==shared:self.emit(f'{flag} = true;',level)

    def region(self, label, stop, level, seen):
        previous=self.origin_context
        try:self._region(label,stop,level,seen)
        finally:self.origin_context=previous

    def _region(self, label, stop, level, seen):
        while label != stop and label != 'exit':
            self.origin_context=(self.source_index.terms[label],)
            if any(label in (shared,join) for shared,join,_ in self.shared_region_stack):return
            if label in self.loop_headers and label not in self.active_loop_headers:
                loop=self.loop_headers[label]
                self.emit('[loop] while (true) {', level)
                self.active_loop_headers.add(label)
                self.region(label, loop['exit'], level + 1, seen)
                self.active_loop_headers.remove(label)
                self.emit('}', level)
                label = loop['exit']
                continue
            if label in seen:
                # Shared scalar comparisons/discard tails can be emitted in
                # mutually exclusive arms of an already validated acyclic CFG.
                # Resource/quad/UAV operations and output writes remain rejected.
                lines = self.blocks[label]
                scalar_tail = all(re.fullmatch(r'%\d+ = (?:phi|fcmp|icmp|select) .+', line) or
                           line == 'call void @dx.op.discard(i32 82, i1 true)'
                           for line in lines[:-1])
                reporting_tail = all(re.fullmatch(r'%\d+ = (?:extractvalue|and|or|xor|shl|lshr) .+', line) or
                                     re.fullmatch(r'%\d+ = call %dx.types.Handle @dx.op.annotateHandle\(.+\)', line) or
                                     re.fullmatch(r'%\d+ = call i32 @dx.op.atomicBinOp.i32\(.+\)', line)
                                     for line in lines[:-1])
                reporting_tail = reporting_tail and any('@dx.op.atomicBinOp.i32(' in line for line in lines[:-1])
                for line in lines[:-1]:
                    atomic = re.match(r'(%\d+) = call i32 @dx.op.atomicBinOp.i32', line)
                    if atomic and any(re.search(re.escape(atomic[1]) + r'(?!\d)', other)
                                      for instructions in self.blocks.values() for other in instructions if other != line):
                        reporting_tail = False
                if not scalar_tail and not reporting_tail:
                    raise ReconstructionError('不支持具有交叉边的非结构控制流：'+label)
                if scalar_tail:
                    self.duplicated_scalar_blocks.add(label)
                else:
                    # Mutually exclusive structured arms preserve one execution per
                    # original path. Only reporting atomics with unused results;
                    # no loads, stores, derivatives or carried atomic values.
                    self.duplicated_reporting_blocks.add(label)
            seen.add(label)
            instructions = self.blocks[label]
            self.emit(f'// DXIL block {label}', level,origins=(self.source_index.labels[label],))
            for position,line in enumerate(instructions[:-1]):
                self.instruction(line, level,origin=self.source_index.positions[(label,position)])
            if self.mesh_pending_output is not None:
                raise ReconstructionError('网格向量输出在基本块末尾缺少分量。')
            term = instructions[-1]
            if label in self.loop_latches:
                loop=self.loop_latches[label]
                branch = re.fullmatch(r'br i1 (%\d+|true|false), label %([\w.]+), label %([\w.]+)(?:, !llvm.loop !\d+)?', term)
                if not branch:
                    raise ReconstructionError('无法解析循环尾部条件。')
                condition = self.operand(branch[1], 'bool')
                if branch[2] != loop['exit']:
                    condition = f'!({condition})'
                self.emit(f'if ({condition}) {{', level)
                self.edge(label, loop['exit'], level + 1)
                self.emit('break;', level + 1)
                self.emit('}', level)
                self.edge(label, loop['header'], level)
                return
            if term == 'ret void':
                self.emit('return output;' if self.outputs and not self.mesh else 'return;', level)
                return
            jump = re.fullmatch(r'br label %([\w.]+)', term)
            if jump:
                target = jump[1]
                self.edge(label, target, level)
                label = target
                continue
            branch = re.fullmatch(r'br i1 (%\d+|true|false), label %([\w.]+), label %([\w.]+)', term)
            if not branch:
                raise ReconstructionError('不支持此分支。')
            left, right = branch[2], branch[3]
            common = self.postdom[left] & self.postdom[right]
            join = next((x for x in common if common <= self.postdom[x]), None)
            if join is None:
                raise ReconstructionError('无法定位分支合并。')
            shared=None
            if self.profile.startswith('cs_'):
                # A shared block immediately before the postdominating join
                # must execute once, after either incoming arm. Edge copies
                # retain its phi inputs; a separate reached flag retains the
                # bypass path. Never duplicate resource reads or collectives.
                def reaches(start,target):
                    pending=[start];visited=set()
                    while pending:
                        current=pending.pop()
                        if current==target:return True
                        if current in visited or current==join:continue
                        visited.add(current);pending.extend(self.successors[current])
                    return False
                candidates=[b for b in self.blocks if b not in (label,join,left,right) and self.successors[b]==[join]
                            and reaches(left,b) and reaches(right,b)]
                if len(candidates)==1:shared=candidates[0]
                if self.loop:
                    # A loop-containing CS may share a whole single-entry
                    # region before the join. Find the common region on the
                    # DAG obtained by cutting the validated loop backedges.
                    def region_nodes(start):
                        pending=[start];nodes=set()
                        while pending:
                            b=pending.pop()
                            if b in nodes or b==join:continue
                            nodes.add(b)
                            pending.extend(c for c in self.successors[b]
                                if not (b in self.loop_latches and c==self.loop_latches[b]['header']))
                        return nodes
                    common_nodes=region_nodes(left)&region_nodes(right)
                    roots=[b for b in common_nodes if not any(b in self.successors[p]
                        and not (p in self.loop_latches and b==self.loop_latches[p]['header']) for p in common_nodes)]
                    if len(roots)==1:
                        root=roots[0]
                        # No edge may enter the common region below its root.
                        arms=region_nodes(left)|region_nodes(right)
                        if all(b==root or all(p in common_nodes for p in arms if b in self.successors[p]) for b in common_nodes):
                            shared=root
            if shared:
                flag=f'reached_{shared}'
                self.emit(f'bool {flag} = false;',level)
                self.shared_region_stack.append((shared,join,flag))
            self.emit(f'if ({self.operand(branch[1], "bool")}) {{', level)
            self.edge(label, left, level + 1)
            self.region(left, join, level + 1, seen)
            self.emit('} else {', level)
            self.edge(label, right, level + 1)
            self.region(right, join, level + 1, seen)
            self.emit('}', level)
            if shared:
                self.shared_region_stack.pop()
                self.emit(f'if ({flag}) {{',level)
                self.region(shared,join,level+1,seen)
                self.emit('}',level)
                self.structured_shared_blocks.add(shared)
            label = join

    def reconstruct(self):
        global_end = len(self.lines)
        self.lines.extend(entry_attributes(self.ir))
        from .compute import COMPUTE_PARAMETERS
        parameters=COMPUTE_PARAMETERS if self.profile.startswith('cs_') else 'ShaderInput input' if self.inputs else ''
        if self.mesh:
            parameters=('uint3 groupID : SV_GroupID, uint3 groupThreadID : SV_GroupThreadID, uint groupIndex : SV_GroupIndex, '
                        f'out vertices ShaderOutput meshVertices[{self.mesh["max_vertices"]}], out indices uint3 meshIndices[{self.mesh["max_primitives"]}], '
                        f'out primitives MeshPrimitive meshPrimitives[{self.mesh["max_primitives"]}]')
        self.lines.append(f'{"ShaderOutput" if self.outputs and not self.mesh else "void"} {self.entry}({parameters}) {{')
        if self.outputs and not self.mesh:
            self.emit('ShaderOutput output;', 1)
        for name, (_, type, _) in self.phis.items():
            qualifier = 'precise ' if self.precise and type in ('float','min16float') else ''
            self.emit(f'{qualifier}{type} v_{name[1:]}; // phi result; assigned on each incoming edge', 1,
                      origins=(self.source_index.definitions[name],))
            self.values[name] = Value(type, 'v_' + name[1:])
        seen = set()
        self.region('0', 'exit', 1, seen)
        if seen != set(self.blocks):
            raise ReconstructionError('控制流未完整重建。')
        self.lines.append('}')
        extra=list(self.extra_types.values())
        self.lines[global_end:global_end] = extra
        self.line_origins={(i+len(extra) if i>=global_end else i):rows for i,rows in self.line_origins.items()}
        from .source_map import expand_source_map
        source_map=expand_source_map(self.lines,self.line_origins)
        source = '\n'.join(self.lines) + '\n'
        if len(self.loops)>1:
            source='// Nested-loop reconstruction: compilation and interface checks do not establish dataflow equivalence.\n// Verify both loop transitions and ordered memory/atomic/collective traces before claiming equivalent behavior.\n'+source
            source_map=[[],[]]+source_map
        if self.native_half:
            source = '#define MMTR_NATIVE_16BIT_TYPES 1\n' + re.sub(r'\bmin16float\b','float16_t',source)
            source_map=[[]]+source_map
        return {'source': source, 'entry': self.entry, 'profile': self.profile,
                'source_map':source_map,'origin_ir':self.ir,
                'duplicated_reporting_blocks': sorted(self.duplicated_reporting_blocks),
                'structured_shared_blocks':sorted(self.structured_shared_blocks),
                'instructions': self.instructions, 'blocks': len(self.blocks),
                'duplicated_scalar_blocks': sorted(self.duplicated_scalar_blocks),
                'natural_loop': ({k: v for k, v in self.loop.items() if k != 'blocks'} if self.loop else None),
                'natural_loops':[{k:v for k,v in loop.items() if k!='blocks'} for loop in self.loops],
                'limitations': '支持受限PS/VS/CS源码重建。CS可保留两层归约循环、groupshared float、barrier9、指定wave及typed64/2D原子；数据流验证另核对两层类型状态映射、初始/反馈/退出条件及有序事件轨迹，不能只据编译成功认定等价。未知操作/布局/CFG拒绝；不证明终止性或跨线程GPU行为。'}


def reconstruct_material(ir):
    return MaterialTranslator(ir).reconstruct()
