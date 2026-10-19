"""Conservative scalar DXIL dataflow/CFG comparison for reconstructed shaders.

Ignores SSA numbering, duplicate pure definitions, commutative operand order,
and optimizer flags. This is structural evidence, not an IEEE/GPU equivalence
proof. CFG path guards are compared; unknown effects are rejected. One natural
output-state/pending loop is checked with an arbitrary-state transition summary.
"""
import hashlib
import json
import re
import struct
from .material_hlsl import split_operands, strip_type
from .reconstruct import ReconstructionError, function_body


class Graph:
    def __init__(self, ir, compute_state_keys=None, *, nested_compute=False, nested_state_keys=None):
        self.ir, self.definition_positions = ir, {}
        metadata = dict(re.findall(r'^!(\d+) = !\{(.*)\}$',ir,re.M))
        model_root = re.search(r'^!dx.shaderModel = !\{!(\d+)\}$',ir,re.M)
        self.vertex = bool(model_root and re.fullmatch(r'!"vs", i32 6, i32 \d+',metadata.get(model_root[1],'')))
        self.compute = bool(model_root and re.fullmatch(r'!"cs", i32 6, i32 \d+',metadata.get(model_root[1],'')))
        self.mesh=None
        if model_root and re.fullmatch(r'!"ms", i32 6, i32 \d+',metadata.get(model_root[1],'')):
            from .mesh import mesh_interface
            self.mesh=mesh_interface(ir)
            self.compute_threads=self.mesh['threads']
        if self.compute:
            from .compute import compute_properties
            from .reconstruct import shader_interface
            shader_interface(ir)
            _,self.compute_threads=compute_properties(ir)
        if self.vertex and re.search(r'@dx.op.(?:discard|atomicBinOp|rawBufferStore)(?:\.|\()',function_body(ir)[1]):
            raise ReconstructionError('顶点输出校验仅支持无discard及可写内存的入口。')
        self.blocks, self.defs, self.nodes, self.cache, self.origins = {'0': []}, {}, {}, {}, {}
        self.bdd_nodes, self.bdd_cache, self.reach_cache = {}, {}, {}
        self.boolean_cache = {}
        self.kill_entry_cache, self.kill_exit_cache = {}, {}
        current = '0'
        self.precise_results = set()
        from .instruction_metadata import precise_instruction
        for raw in function_body(ir)[1].splitlines():
            label = re.match(r'(?:; <label>:(\d+)|([\w.]+):)', raw.strip())
            if label:
                current = label[1] or label[2]
                self.blocks[current] = []
            else:
                line = raw.split(';', 1)[0].strip()
                line, precise_result = precise_instruction(line, metadata)
                if precise_result:
                    self.precise_results.add(precise_result)
                line = re.sub(r', !llvm.loop !\d+$', '', line)
                line = re.sub(r', !dx\.controlflow\.hints !\d+$', '', line)
                if line:
                    self.blocks[current].append(line)
                    definition = re.fullmatch(r'(%[\w.]+) = (.+)', line)
                    if definition:
                        self.defs[definition[1]] = (current, definition[2])
                        self.definition_positions[definition[1]] = len(self.blocks[current]) - 1
        from .control_flow import single_natural_loop
        from .local_array import analyze_local_arrays
        from .shared_memory import analyze_shared_memory
        self.shared_arrays,self.shared_pointers,self.shared_loads,self.shared_stores,self.shared_barriers=analyze_shared_memory(ir,self.blocks)
        if self.shared_arrays and not self.compute:raise ReconstructionError('groupshared事件校验仅支持CS。')
        self.local_arrays, self.local_pointers, self.local_loads, self.local_stores, self.local_ignored = analyze_local_arrays(self.blocks)
        self.nested_loops = []
        self.loop_parameters, self.loop_states = {}, []
        if nested_compute:
            if not self.compute:
                raise ReconstructionError('嵌套事件模型仅支持CS。')
            from .nested_compute import prepare_nested_compute
            self.loop = None
            prepare_nested_compute(self, nested_state_keys)
        else:
            self.loop = single_natural_loop(self.blocks)
        if self.loop and (self.shared_arrays or self.shared_barriers):
            raise ReconstructionError('groupshared与循环的跨迭代事件尚未建模。')
        if self.loop and self.local_arrays:
            raise ReconstructionError('局部数组与循环组合尚未验证。')
        if self.mesh and (self.loop or self.local_arrays):
            raise ReconstructionError('网格验证暂不支持循环或私有数组。')
        if self.loop:
            if self.compute:
                from .compute_loop import prepare_compute_loop
                prepare_compute_loop(self,compute_state_keys)
            else:self.prepare_loop()
        self.order, visiting, visited = [], set(), set()

        def visit(label):
            if label in visiting:
                raise ReconstructionError('循环不在数据流校验范围。')
            if label in visited:
                return
            if label not in self.blocks:
                raise ReconstructionError('未知基本块。')
            visiting.add(label)
            self.order.append(label)
            for child in re.findall(r'label %([\w.]+)', self.blocks[label][-1]):
                visit(child)
            visiting.remove(label)
            visited.add(label)
        visit('0')
        if visited != set(self.blocks):
            raise ReconstructionError('不可达块不在校验范围。')
        # One reporting atomic after resource reads is sufficient for the skin
        # entry. Do not silently treat general UAV memory operations as pure.
        atomic = [(label, i) for label, lines in self.blocks.items() for i, line in enumerate(lines)
                  if '@dx.op.atomicBinOp.' in line]
        self.ordered_memory = bool(self.compute and atomic) or any('@dx.op.rawBufferStore.' in line for lines in self.blocks.values() for line in lines)
        self.memory_positions = {b: [i for i, line in enumerate(lines) if re.search(r'@dx.op.(?:atomicBinOp|rawBufferStore)\.', line)]
                                 for b, lines in self.blocks.items()}
        if self.ordered_memory:
            if self.loop and not self.compute:
                raise ReconstructionError('有序可写内存暂不支持循环。')
            for origin, positions in self.memory_positions.items():
                if not positions:
                    continue
                pending, descendants = re.findall(r'label %([\w.]+)', self.blocks[origin][-1]), set()
                while pending:
                    child = pending.pop()
                    if child not in descendants:
                        descendants.add(child)
                        pending.extend(re.findall(r'label %([\w.]+)', self.blocks[child][-1]))
                if not self.compute and any(self.memory_positions[b] for b in descendants):
                    raise ReconstructionError('当前有序内存只支持每条路径在一个基本块内的写入序列。')
        if len(atomic) > 1 and not self.ordered_memory and not self.compute:
            raise ReconstructionError('多个原子操作需要顺序内存建模，当前拒绝校验。')
        self.compute_events = {}
        if self.compute or self.mesh:
            phase_counts={};dynamic_read_positions=[]
            for block,lines in self.blocks.items():
                for position,line in enumerate(lines):
                    m=re.search(r'@dx.op.rawBufferLoad.\w+\(i32 \d+, %dx.types.Handle (%[\w.]+),',line)
                    resource=self.compute_read_resource(m[1]) if m else None
                    uav_read=bool(resource and resource[0]==1)
                    dynamic_read=bool(resource and len(resource)==7 and resource[6]=='dynamic_raw_srv')
                    if dynamic_read:dynamic_read_positions.append((block,position))
                    collective=bool(re.search(r'@dx.op.(?:waveReadLaneFirst|waveAllOp|waveActiveOp|waveAllTrue|waveActiveBit|wavePrefixOp)(?:\.|\()',line))
                    loop_read=bool(m and (self.loop and block in self.loop['blocks'] or any(block in loop['blocks'] for loop in self.nested_loops)))
                    shared_read=line.split(' = ',1)[0] in self.shared_loads
                    mesh_output=bool(self.mesh and re.search(r'@dx.op.(?:setMeshOutputCounts|emitIndices|storeVertexOutput|storePrimitiveOutput)(?:\.|\()',line))
                    if uav_read or dynamic_read or loop_read or collective or shared_read or (block,position) in self.shared_stores or (block,position) in self.shared_barriers or mesh_output or re.search(r'@dx.op.(?:rawBufferStore|atomicBinOp)\.',line):
                        if self.nested_loops:
                            containing=[loop for loop in self.nested_loops if block in loop['blocks']]
                            phase=('nested_iteration',min(containing,key=lambda loop:len(loop['blocks']))['key']) if containing else ('nested_outside',)
                            ordinal=phase_counts.get(phase,0);phase_counts[phase]=ordinal+1
                            self.compute_events[(block,position)]=(phase,ordinal)
                        elif self.loop:
                            phase='iteration' if block in self.loop['blocks'] else 'outside'
                            ordinal=phase_counts.get(phase,0);phase_counts[phase]=ordinal+1
                            self.compute_events[(block,position)]=(phase,ordinal)
                        else:self.compute_events[(block,position)]=len(self.compute_events)
            if dynamic_read_positions and not self.nested_loops and not self.mesh:
                # Mutually exclusive branch layout can reverse during DXC.
                # Identify dynamic read occurrences by their proven ordinal on
                # every path reaching them, not the lexical order of blocks.
                # If paths have differing prefix lengths, this subset rejects.
                predecessors={b:[] for b in self.blocks}
                for b,lines in self.blocks.items():
                    for child in set(re.findall(r'label %([\w.]+)',lines[-1])):
                        predecessors[child].append(b)
                counts={}
                def prefix_counts(block):
                    if block not in counts:
                        counts[block]=({0} if block=='0' else set().union(*(
                            {n+sum(b==parent for b,_ in self.compute_events) for n in prefix_counts(parent)}
                            for parent in predecessors[block])))
                    return counts[block]
                for block,position in dynamic_read_positions:
                    ordinals={n+sum(b==block and p<position for b,p in self.compute_events) for n in prefix_counts(block)}
                    if len(ordinals)!=1:
                        raise ReconstructionError('动态SRV读取需要各到达路径具有相同事件前缀长度。')
                    self.compute_events[(block,position)]=('dynamic_raw_srv_path_ordinal',next(iter(ordinals)))
        memory_starts = [(b, p[0]) for b, p in self.memory_positions.items() if p] if self.ordered_memory else atomic
        for origin, position in memory_starts:
            descendants, pending = set(), re.findall(r'label %([\w.]+)', self.blocks[origin][-1])
            while pending:
                block = pending.pop()
                if block not in descendants:
                    descendants.add(block)
                    pending.extend(re.findall(r'label %([\w.]+)', self.blocks[block][-1]))
            reads = r'@dx.op.(?:cbufferLoadLegacy|textureLoad|rawBufferLoad|sample|sampleGrad|sampleLevel)\.'
            for block, lines in self.blocks.items():
                for i, line in enumerate(lines):
                    if re.search(reads, line) and (block in descendants or block == origin and i > position):
                        cbuffer = re.search(r'@dx.op.cbufferLoadLegacy\.\w+\(i32 59, %dx.types.Handle (%[\w.]+),', line)
                        if self.ordered_memory and cbuffer and self.static_handle(cbuffer[1])[0] == 2:
                            # cbuffers are read-only descriptors, disjoint from UAVs.
                            continue
                        if not self.ordered_memory:
                            atomic_handle = re.search(r'@dx.op.atomicBinOp.i32\(i32 78, %dx.types.Handle (%[\w.]+),', self.blocks[origin][position])
                            read_handle = re.search(r'@dx.op.(?:cbufferLoadLegacy|textureLoad|sampleLevel)\.\w+\(i32 \d+, %dx.types.Handle (%[\w.]+),',line)
                            if atomic_handle and read_handle:
                                writer = self.static_handle(atomic_handle[1])
                                reader = self.static_handle(read_handle[1])
                                if writer[0] == 1 and writer[3] == '%struct.RWByteAddressBuffer' and writer[4] == 'i32 11, i1 false, i1 false, i1 false, null':
                                    # Legal simultaneous bindings cannot make a typed
                                    # texture or CB view of this raw UAV buffer. Do not
                                    # extend this to SRV buffers, heaps or arrays.
                                    if reader[0] == 2 or reader[0] == 0 and re.fullmatch(r'%"class.Texture(?:2D|3D)<(?:float|vector<float, [1-4]> )>"',reader[3]):
                                        continue
                        raise ReconstructionError('原子操作后的资源读取需要别名与内存顺序建模，当前拒绝校验。')
        self.labels = {label: i for i, label in enumerate(self.order)}
        self.constants = {}
        for m in re.finditer(r'^@(\w+) = internal unnamed_addr constant \[(\d+) x float\] \[(.+)\], align 4$', ir, re.M):
            values = split_operands(m[3])
            if len(values) != int(m[2]):
                raise ReconstructionError('不完整的常量数组。')
            self.constants[m[1]] = int(m[2]), tuple(self.typed(value) for value in values)
        self.predecessors = {label: [] for label in self.blocks}
        for label, lines in self.blocks.items():
            for child in set(re.findall(r'label %([\w.]+)', lines[-1])):
                self.predecessors[child].append(label)
        self.structured = {}
        nodes = dict(re.findall(r'^!(\d+) = !\{(.*)\}$', ir, re.M))
        root = re.search(r'^!dx.resources = !\{!(\d+)\}', ir, re.M)
        if root:
            srv = nodes[root[1]].split(', ')[0]
            for rid in re.findall(r'!(\d+)', nodes.get(srv[1:], '')) if srv != 'null' else []:
                m = re.fullmatch(r'i32 (\d+), %"(?:hostlayout\.)?class\.StructuredBuffer<[^"\n]+>"\* undef, !"[^"\n]*", i32 (\d+), i32 (\d+), i32 1, i32 12, i32 0, !(\d+)', nodes[rid])
                props = re.fullmatch(r'i32 1, i32 (\d+)', nodes.get(m[4], '')) if m else None
                if props:
                    self.structured[int(m[1])] = (int(m[2]), int(m[3]), int(props[1]))
        self.nested_counter_invariants = []
        if self.nested_loops:
            from .nested_compute import install_counter_invariants
            install_counter_invariants(self)

    def static_handle(self, name):
        """Resolve singleton bound descriptors only; heaps/arrays remain rejected here."""
        if name not in self.defs:
            raise ReconstructionError('有序内存需要静态绑定的资源句柄。')
        body = self.defs[name][1]
        m = re.fullmatch(r'call %dx.types.Handle @dx.op.(\w+)\((.*)\)', body)
        if not m:
            raise ReconstructionError('未知有序内存句柄。')
        args = [strip_type(arg) for arg in split_operands(m[2])]
        if m[1] == 'annotateHandle':
            resource = self.static_handle(args[1])
            props = re.fullmatch(r'\{ i32 (\d+), i32 (\d+) \}', args[2])
            expected = {(13, int(resource[4].split(',')[0][4:]))} if resource[0] == 2 else set()
            if resource[0] == 1:
                if resource[3] == '%struct.RWByteAddressBuffer':
                    expected = {(4107, 0)}
                else:
                    meta = re.fullmatch(r'i32 12, i1 false, i1 false, i1 false, !(\d+)', resource[4])
                    stride = re.fullmatch(r'i32 1, i32 (\d+)(?:, i32 3, i32 1)?', resource[5].get(meta[1], '')) if meta else None
                    if stride:
                        expected = {(4108, int(stride[1])), (4620, int(stride[1]))}
                    texture_props=re.fullmatch(r'i32 2, i1 false, i1 false, i1 false, !(\d+)',resource[4])
                    if resource[3]=='%"class.RWTexture2D<unsigned int>"' and texture_props and resource[5].get(texture_props[1])=='i32 0, i32 5':
                        expected={(4098,261)}
            if resource[0] == 0:
                texture = re.fullmatch(r'%"class.Texture(2D|3D)<(?:float|vector<float, ([1-4])> )>"',resource[3])
                if texture and resource[4].startswith('i32 '+('2' if texture[1]=='2D' else '4')+','):
                    expected = {(2 if texture[1]=='2D' else 4, int(texture[2] or 1)*256+9)}
                if self.compute or self.mesh:
                    if resource[3]=='%struct.ByteAddressBuffer' and resource[4]=='i32 11, i32 0, null':
                        expected={(11,0)}
                    structured=re.fullmatch(r'%"(?:hostlayout\.)?class.StructuredBuffer<[^"\n]+>"',resource[3])
                    stride_ref=re.fullmatch(r'i32 12, i32 0, !(\d+)',resource[4])
                    stride=re.fullmatch(r'i32 1, i32 (\d+)',resource[5].get(stride_ref[1],'')) if stride_ref else None
                    if structured and stride:expected={(12,int(stride[1])),(524,int(stride[1]))}
            if props is None or tuple(map(int, props.groups())) not in expected:
                raise ReconstructionError('有序内存资源注解与静态绑定不符。')
            return resource
        if m[1] == 'createHandle' and len(args) == 5 and args[4] == 'false' and all(x.isdigit() for x in args[1:4]):
            cls, rid, index = map(int, args[1:4])
            nodes = dict(re.findall(r'^!(\d+) = !\{(.*)\}$', self.ir, re.M))
            root = re.search(r'^!dx.resources = !\{!(\d+)\}', self.ir, re.M)
            side = nodes[root[1]].split(', ')[{0: 0, 1: 1, 2: 2, 3: 3}[cls]] if root and cls in (0,1,2,3) else 'null'
            for node in re.findall(r'!(\d+)', nodes.get(side[1:], '')):
                entry = re.fullmatch(r'i32 (\d+), (.+)\* undef, !"[^"\n]*", i32 (\d+), i32 (\d+), i32 1, (.+)', nodes[node])
                if entry and int(entry[1]) == rid and int(entry[4]) == index:
                    return cls, int(entry[3]), index, entry[2], entry[5], nodes
        if m[1] == 'createHandleFromBinding' and len(args) == 4 and args[3] == 'false':
            declaration='{ i32 0, i32 0, i32 0, i8 0 }' if args[1]=='zeroinitializer' else args[1]
            binding = re.fullmatch(r'\{ i32 (\d+), i32 \1, i32 (\d+), i8 ([0-3]) \}', declaration)
            if binding and args[2] == binding[1]:
                cls, space, index = int(binding[3]), int(binding[2]), int(binding[1])
                nodes = dict(re.findall(r'^!(\d+) = !\{(.*)\}$', self.ir, re.M))
                root = re.search(r'^!dx.resources = !\{!(\d+)\}', self.ir, re.M)
                side = nodes[root[1]].split(', ')[cls] if root else 'null'
                for node in re.findall(r'!(\d+)', nodes.get(side[1:], '')):
                    entry = re.fullmatch(r'i32 \d+, (.+)\* undef, !"[^"\n]*", i32 '+str(space)+r', i32 '+str(index)+r', i32 1, (.+)', nodes[node])
                    if entry:
                        return cls, space, index, entry[1], entry[2], nodes
        raise ReconstructionError('有序内存不支持动态、无界或无法匹配的资源绑定。')

    def compute_read_resource(self, name):
        """Classify acyclic raw SRV arrays/heaps without relaxing UAV bindings.

        Exact handle expressions (including descriptor index/nonuniform flag)
        remain part of occurrence-bearing read events. Post-write reads fail the
        alias check; dynamic descriptors are not immutable across writes.
        """
        if (self.compute or self.mesh) and not self.loop and name in self.defs:
            body=self.defs[name][1]
            heap=re.fullmatch(r'call %dx.types.Handle @dx.op.annotateHandle\(i32 216, %dx.types.Handle (%[\w.]+), %dx.types.ResourceProperties \{ i32 11, i32 0 \}\)',body)
            if heap:
                inner=self.defs.get(heap[1],('', ''))[1]
                if re.fullmatch(r'call %dx.types.Handle @dx.op.createHandleFromHeap\(i32 218, i32 (?:%[\w.]+|\d+), i1 false, i1 (?:true|false)\)',inner):
                    return 0,None,None,'%struct.ByteAddressBuffer','i32 11, i32 0, null',{},'dynamic_raw_srv'
            array=re.fullmatch(r'call %dx.types.Handle @dx.op.createHandle\(i32 57, i8 0, i32 (\d+), i32 (?:%[\w.]+|\d+), i1 (?:true|false)\)',body)
            if array:
                nodes=dict(re.findall(r'^!(\d+) = !\{(.*)\}$',self.ir,re.M))
                root=re.search(r'^!dx.resources = !\{!(\d+)\}$',self.ir,re.M)
                side=nodes.get(root[1],'').split(', ')[0] if root else 'null'
                entries=[nodes[n] for n in re.findall(r'!(\d+)',nodes.get(side[1:],''))]
                shape=r'i32 '+array[1]+r', \[0 x %struct.ByteAddressBuffer\]\* undef, !"[^"\n]*", i32 (\d+), i32 (\d+), i32 (?:-1|4294967295), i32 11, i32 0, null'
                matches=[m for e in entries if (m:=re.fullmatch(shape,e))]
                if len(matches)==1:
                    return 0,int(matches[0][1]),int(matches[0][2]),'%struct.ByteAddressBuffer','i32 11, i32 0, null',nodes,'dynamic_raw_srv'
            if self.nested_loops:
                # Exact SM6.6 bound raw-SRV arrays; never dynamic UAVs.
                annotated=re.fullmatch(r'call %dx.types.Handle @dx.op.annotateHandle\(i32 216, %dx.types.Handle (%[\w.]+), %dx.types.ResourceProperties \{ i32 11, i32 0 \}\)',body)
                inner=self.defs.get(annotated[1],('', ''))[1] if annotated else ''
                bound=re.fullmatch(r'call %dx.types.Handle @dx.op.createHandleFromBinding\(i32 217, %dx.types.ResBind \{ i32 (\d+), i32 (?:-1|4294967295), i32 (\d+), i8 0 \}, i32 (?:%[\w.]+|\d+), i1 (?:true|false)\)',inner)
                if bound:
                    nodes=dict(re.findall(r'^!(\d+) = !\{(.*)\}$',self.ir,re.M))
                    root=re.search(r'^!dx.resources = !\{!(\d+)\}$',self.ir,re.M)
                    side=nodes.get(root[1],'').split(', ')[0] if root else 'null'
                    entries=[nodes[n] for n in re.findall(r'!(\d+)',nodes.get(side[1:],''))]
                    shape=r'i32 \d+, \[0 x %struct.ByteAddressBuffer\]\* undef, !"[^"\n]*", i32 '+bound[2]+r', i32 '+bound[1]+r', i32 (?:-1|4294967295), i32 11, i32 0, null'
                    if sum(bool(re.fullmatch(shape,e)) for e in entries)==1:
                        return 0,int(bound[2]),int(bound[1]),'%struct.ByteAddressBuffer','i32 11, i32 0, null',nodes,'dynamic_raw_srv'
        return self.static_handle(name)

    def compute_event_identity(self,block,position):
        if not self.nested_loops and not self.mesh:
            return self.compute_events[(block,position)]
        from .nested_compute import event_prefix_identity
        return event_prefix_identity(self,block,position)

    def prepare_loop(self):
        """Cut one natural-loop backedge and compare an arbitrary-state step.

        State identities come from exact output slots, never SSA/phi ordering.
        One additional i32 state is allowed (the per-lane pending value). This
        does not unroll a fixed iteration count or replace any loop computation.
        """
        loop = self.loop
        if any('@dx.op.atomicBinOp.' in line for b in loop['blocks'] for line in self.blocks[b]):
            raise ReconstructionError('循环中的原子或可写内存需要跨迭代内存建模。')
        states = []
        for line in self.blocks[loop['header']][:-1]:
            match = re.fullmatch(r'(%[\w.]+) = phi (float|i32) (.+)', line)
            if ' = phi ' in line and not match:
                raise ReconstructionError('不支持此循环状态类型。')
            if match:
                edges = {pred: value for value, pred in re.findall(r'\[ ([^,]+), %([\w.]+) \]', match[3])}
                if set(edges) != {loop['preheader'], loop['latch']}:
                    raise ReconstructionError('循环状态 phi 的入口/回边不完整。')
                states.append({'name': match[1], 'type': match[2], 'initial': edges[loop['preheader']],
                               'feedback': edges[loop['latch']], 'outputs': set()})
        if not states or len({s['feedback'] for s in states}) != len(states):
            raise ReconstructionError('无法唯一识别循环状态。')
        by_feedback = {s['feedback']: s for s in states}

        def suffix_values(value, visiting=None):
            if value in by_feedback:
                return {value}
            if not value.startswith('%'):
                return set()
            if value not in self.defs:
                raise ReconstructionError('循环退出值未定义。')
            block, body = self.defs[value]
            if block in loop['blocks']:
                raise ReconstructionError('循环退出值未通过已识别状态。')
            match = re.fullmatch(r'phi (?:float|i32) (.+)', body)
            if not match:
                raise ReconstructionError('循环状态身份只支持直接输出 phi 链。')
            visiting = set() if visiting is None else visiting
            if value in visiting:
                raise ReconstructionError('循环退出值出现额外递归。')
            result = set()
            for incoming, _ in re.findall(r'\[ ([^,]+), %([\w.]+) \]', match[1]):
                result.update(suffix_values(incoming, visiting | {value}))
            return result

        for block, lines in self.blocks.items():
            if block in loop['blocks']:
                if any('@dx.op.storeOutput.' in line for line in lines):
                    raise ReconstructionError('循环内输出写入需要跨迭代顺序建模。')
                continue
            for line in lines:
                store = re.fullmatch(r'call void @dx.op.storeOutput\.(?:f32|i32)\((.*)\)', line)
                if store:
                    args = split_operands(store[1])
                    state_values = suffix_values(strip_type(args[4]))
                    if len(state_values) > 1:
                        raise ReconstructionError('同一输出关联多个循环状态。')
                    for value in state_values:
                        by_feedback[value]['outputs'].add(tuple(args[1:4]))
        pending = [s for s in states if not s['outputs']]
        if len(pending) != 1 or pending[0]['type'] != 'i32':
            raise ReconstructionError('当前循环只允许一个未直接输出的 i32 pending 状态。')
        keys = set()
        for state in states:
            key = ('output_slots', tuple(sorted(state['outputs']))) if state['outputs'] else ('pending',)
            if key in keys:
                raise ReconstructionError('循环状态身份不唯一。')
            keys.add(key)
            state['key'] = key
            self.loop_parameters[state['name']] = (state['type'], key)
        self.loop_states = states
        sink = '__loop_backedge'
        if sink in self.blocks:
            raise ReconstructionError('循环校验保留块名称冲突。')
        self.blocks[loop['latch']][-1] = re.sub(r'label %' + re.escape(loop['header']) + r'(?=[,\s]|$)',
                                              'label %' + sink, self.blocks[loop['latch']][-1])
        self.blocks[sink] = ['ret void']
        loop['backedge_sink'] = sink

    def structured_stride(self, handle):
        """Resolve only statically bound read-only structured handles."""
        if handle not in self.defs:
            return None
        body = self.defs[handle][1]
        m = re.fullmatch(r'call %dx.types.Handle @dx.op.(\w+)\((.*)\)', body)
        if not m:
            return None
        args = [strip_type(x) for x in split_operands(m[2])]
        if m[1] == 'annotateHandle':
            return self.structured_stride(args[1])
        if m[1] == 'createHandle' and args[1] == '0' and args[2].isdigit():
            item = self.structured.get(int(args[2]))
            if item and args[3] == str(item[1]) and args[4] == 'false':
                return item[2]
        if m[1] == 'createHandleFromBinding':
            binding = '{ i32 0, i32 0, i32 0, i8 0 }' if args[1] == 'zeroinitializer' else args[1]
            b = re.fullmatch(r'\{ i32 (\d+), i32 \1, i32 (\d+), i8 0 \}', binding)
            matches = [v[2] for v in self.structured.values() if b and v[:2] == (int(b[2]), int(b[1]))]
            if len(matches) == 1 and args[2] == b[1] and args[3] == 'false':
                return matches[0]
        return None

    def structured_scalar(self, aggregate, lane):
        if (self.compute or self.mesh) and (self.defs[aggregate][0],self.definition_positions[aggregate]) in self.compute_events:
            return None
        m = re.fullmatch(r'call %dx.types.ResRet\.(f32|i32) @dx.op.rawBufferLoad\.\1\((.*)\)', self.defs[aggregate][1])
        if m is None:
            return None
        typed = split_operands(m[2])
        args = [strip_type(x) for x in typed]
        stride = self.structured_stride(args[1])
        if stride is None:
            return None
        if args[0] != '139' or not args[3].isdigit() or not args[4].isdigit() or args[5] != '4':
            raise ReconstructionError('结构化读取不在标量校验范围。')
        offset, mask = int(args[3]), int(args[4])
        if not 0 < mask < 16 or offset % 4 or not 0 <= lane < 4 or not (mask & (1 << lane)) or offset + mask.bit_length()*4 > stride:
            raise ReconstructionError('结构化读取未定义通道或越界。')
        # Read-only, aligned, within one declared element; status values are never
        # normalized. DXC may reduce float4 row loads to a single scalar at +12.
        return self.intern(('structured_scalar_load', m[1], self.typed(typed[1]),
                            self.typed(typed[2]), offset + lane*4, stride))

    def intern(self, value):
        # A pure per-value operation can be applied on either side of an
        # acyclic scalar choice. This preserves the operation and each exact
        # guard; reads/events and floating-point arithmetic are excluded.
        choice_args=None
        if self.mesh and value[:3] in tuple((op,None,'i32') for op in ('or','and','xor','lshr','ashr','shl')):
            choice_args=value[3];result_type='i32';arg_slot=3
        elif self.mesh and value[:4]==('call','float','legacyF16ToF32',None):
            choice_args=value[4];result_type='float';arg_slot=4
        if choice_args:
            for index,arg in enumerate(choice_args):
                node=self.nodes[arg]
                if node[0]=='guarded_phi':
                    if len(node[2])>32:raise ReconstructionError('网格纯值分支超出32项上限。')
                    cases={}
                    for incoming,guard in node[2]:
                        args=list(choice_args);args[index]=incoming
                        if arg_slot==3 and value[0] in ('or','and','xor'):args.sort()
                        changed=list(value);changed[arg_slot]=tuple(args)
                        self.phi_cases(self.intern(tuple(changed)),guard,cases)
                    return next(iter(cases)) if len(cases)==1 else self.intern(('guarded_phi',result_type,tuple(sorted(cases.items()))))
        # Bitwise integer regrouping is exact, including packed half/normal
        # fields. Do not apply this rule to float arithmetic or boolean BDDs.
        if value[:3] in (('or',None,'i32'),('and',None,'i32'),('xor',None,'i32')):
            terms=[]
            for key in value[3]:
                child=self.nodes[key]
                terms.extend(child[3] if child[:3]==value[:3] else (key,))
            value=value[:3]+(tuple(sorted(terms)),)
        key = hashlib.sha256(json.dumps(value, separators=(',', ':')).encode('utf-8')).hexdigest()
        self.nodes[key] = value
        return key

    def value(self, value, type):
        value = value.strip()
        if re.fullmatch(r'%[\w.]+', value):
            return self.expression(value)
        if value in ('undef', 'true', 'false', 'zeroinitializer'):
            return self.intern((type, value))
        if type == 'float':
            number = struct.unpack('>d', bytes.fromhex(value[2:]))[0] if value.startswith('0x') else float(value)
            return self.intern(('float32_bits', struct.pack('<f', number).hex()))
        if type == 'half':
            if not re.fullmatch(r'0xH[0-9A-Fa-f]{4}', value):
                raise ReconstructionError('未知half常量格式。')
            return self.intern(('float16_bits', value[3:].lower()))
        if re.fullmatch(r'i\d+', type) and re.fullmatch(r'-?\d+', value):
            return self.intern((type, int(value) % (1 << int(type[1:]))))
        return self.intern((type, value))

    def typed(self, text):
        type, value = text.split(' ', 1)
        return self.value(value, type)

    def bitcast32(self, value, target):
        node = self.nodes[value]
        if node[0] == 'guarded_phi':
            cases = {}
            for incoming, guard in node[2]:
                self.phi_cases(self.bitcast32(incoming, target), guard, cases)
            return next(iter(cases)) if len(cases) == 1 else self.intern(('guarded_phi', target, tuple(sorted(cases.items()))))
        if node in (('float','undef'),('i32','undef')):
            return self.value('undef', target)
        if node[0] == 'select':
            condition, left, right = node[1]
            return self.intern(('select', (condition, self.bitcast32(left, target), self.bitcast32(right, target))))
        if node[0] == 'bitcast' and node[2] != target:
            return node[1]
        if node[0] == 'i32' and isinstance(node[1], int) and target == 'float':
            return self.intern(('float32_bits', node[1].to_bytes(4, 'little').hex()))
        if node[0] == 'float32_bits' and target == 'i32':
            return self.intern(('i32', int.from_bytes(bytes.fromhex(node[1]), 'little')))
        return self.intern(('bitcast', value, target))

    def integer_add(self, args):
        """Exact modulo32 add, including optimizer motion through finite phis."""
        terms, constant = [], 0
        for arg in args:
            node = self.nodes[arg]
            expanded = node[3] if node[:3] == ('add',None,'i32') else (arg,)
            for value in expanded:
                item = self.nodes[value]
                if item[0] == 'i32' and isinstance(item[1],int):
                    constant = (constant + item[1]) & 0xffffffff
                else:
                    terms.append(value)
        if constant or not terms:
            terms.append(self.value(str(constant),'i32'))
        for i,arg in enumerate(terms):
            node = self.nodes[arg]
            if node[:2] == ('guarded_phi','i32') and len(node[2]) <= (32 if self.mesh else 2):
                cases = {}
                for value,guard in node[2]:
                    result = self.integer_add(terms[:i]+[value]+terms[i+1:])
                    self.phi_cases(result,guard,cases)
                return next(iter(cases)) if len(cases)==1 else self.intern(('guarded_phi','i32',tuple(sorted(cases.items()))))
        return terms[0] if len(terms)==1 else self.intern(('add',None,'i32',tuple(sorted(terms))))

    def half_limited_intrinsic(self, value):
        """Recognize only half->float saturate/max->half, preserving rounding.

        Saturation selects a half input or exactly representable 0/1, with
        NaN->0 at either width. Max additionally requires a positive normal
        half constant, excluding signed-zero ties and half-subnormal outputs.
        No float arithmetic association or general cast is normalized here.
        """
        node = self.nodes[value]
        if node[:3] not in (('call','float','unary.f32'),('call','float','binary.f32')) or node[3] is not None:
            return None
        opcode = self.nodes[node[4][0]]
        if not (node[2] == 'unary.f32' and opcode == ('i32',7) and len(node[4]) == 2
                or node[2] == 'binary.f32' and opcode == ('i32',35) and len(node[4]) == 3):
            return None
        operands, positive_normal = [], False
        for arg in node[4][1:]:
            source = self.nodes[arg]
            if source[0] == 'fpext' and source[2] == 'float':
                operands.append(source[1])
            elif source[0] == 'float32_bits':
                number = struct.unpack('<f',bytes.fromhex(source[1]))[0]
                try:
                    bits = struct.pack('>e',number)
                except (OverflowError,struct.error):
                    return None
                if not (-65504 <= number <= 65504) or struct.unpack('>e',bits)[0] != number:
                    return None
                half_bits = int.from_bytes(bits,'big')
                positive_normal |= 0x0400 <= half_bits < 0x7c00
                operands.append(self.intern(('float16_bits',bits.hex())))
            else:
                return None
        if node[2] == 'binary.f32' and not positive_normal:
            return None
        return self.intern(('call','half',node[2].replace('f32','f16'),None,(node[4][0],*operands)))

    def bdd(self, atom, low='0', high='1'):
        if low == high:
            return low
        key = self.intern(('boolean_decision', atom, low, high))
        self.bdd_nodes[key] = atom, low, high
        return key

    def logic(self, op, a, b):
        a, b = sorted((a, b))
        key = op, a, b
        if key in self.bdd_cache:
            return self.bdd_cache[key]
        if a in ('0', '1') and b in ('0', '1'):
            result = str(int((a == '1' and b == '1') if op == 'and' else
                             (a == '1' or b == '1') if op == 'or' else a != b))
        else:
            atom = min(self.bdd_nodes[x][0] for x in (a, b) if x not in ('0', '1'))
            def sides(value):
                return self.bdd_nodes[value][1:] if value not in ('0', '1') and self.bdd_nodes[value][0] == atom else (value, value)
            al, ah = sides(a)
            bl, bh = sides(b)
            result = self.bdd(atom, self.logic(op, al, bl), self.logic(op, ah, bh))
        self.bdd_cache[key] = result
        return result

    def boolean(self, value):
        if value not in self.boolean_cache:
            self.boolean_cache[value] = self.boolean_expression(value)
        return self.boolean_cache[value]

    def boolean_expression(self, value):
        node = self.nodes[value]
        if node[0]=='boolean_value':return node[1]
        if node == ('i1', 'true'):
            return '1'
        if node == ('i1', 'false'):
            return '0'
        if node[:2]==('guarded_phi','i1'):
            # Boolean phi is a path-selected predicate, not a fresh unrelated
            # condition. Preserve each incoming value AND its exact edge guard.
            result='0'
            for incoming,guard in node[2]:
                result=self.logic('or',result,self.logic('and',guard,self.boolean(incoming)))
            return result
        if node[0] in ('and', 'or', 'xor') and node[2] == 'i1':
            if node[0] == 'and':
                a, b = node[3]
                if self.integer_eq_implies_ne(a, b):
                    return self.boolean(a)
                if self.integer_eq_implies_ne(b, a):
                    return self.boolean(b)
            return self.logic(node[0], *(self.boolean(x) for x in node[3]))
        if node[0] in ('icmp', 'fcmp'):
            op, pred, type, args = node
            if self.nested_loops and op=='icmp' and pred=='ult' and type=='i32':
                for invariant in self.nested_counter_invariants:
                    if args==(invariant['next'],invariant['bound']):
                        # Proven current < bound and exact +1 imply next <=
                        # bound without unsigned overflow. Only here is ult
                        # equivalent to ne; arbitrary comparisons stay exact.
                        return self.logic('xor',self.bdd(self.intern(('icmp','eq','i32',tuple(sorted(args))))),'1')
            # A comparison of a phi evaluates the incoming value selected by
            # its path. Distribute the comparison over those exact path guards.
            # This changes no floating-point predicate or arithmetic.
            for index, operand in enumerate(args):
                incoming = self.nodes[operand]
                if incoming[0] == 'guarded_phi':
                    result = '0'
                    for scalar, guard in incoming[2]:
                        replaced = list(args)
                        replaced[index] = scalar
                        if pred in ('eq', 'ne', 'oeq', 'one', 'ueq', 'une'):
                            replaced.sort()
                        comparison = self.intern((op, pred, type, tuple(replaced)))
                        result = self.logic('or', result,
                                            self.logic('and', guard, self.boolean(comparison)))
                    return result
            complement = {'ne': 'eq', 'ult': 'oge', 'ule': 'ogt', 'ueq': 'one', 'une': 'oeq'}
            if op == 'icmp':
                complement = {'ne': 'eq'}
            if pred in complement:
                pred = complement[pred]
                if pred[-2:] in ('gt', 'ge'):
                    pred = pred[:-2] + {'gt': 'lt', 'ge': 'le'}[pred[-2:]]
                    args = tuple(reversed(args))
                positive = self.bdd(self.intern((op, pred, type, args)))
                return self.logic('xor', positive, '1')
        return self.bdd(value)

    def unsigned_bounds(self, value):
        """Small, conservative unsigned range analysis; no floating-point inference."""
        node = self.nodes[value]
        if node[0] == 'i32' and isinstance(node[1], int):
            return node[1], node[1]
        if node[:3] == ('and', None, 'i32'):
            constants = [self.nodes[x][1] for x in node[3] if self.nodes[x][0] == 'i32' and isinstance(self.nodes[x][1], int)]
            if constants:
                mask = 0xffffffff
                for constant in constants:
                    mask &= constant
                return 0, mask
        return 0, 0xffffffff

    def integer_eq_implies_ne(self, first, second):
        """x == (y & mask) makes x != an out-of-range sentinel redundant."""
        a, b = self.nodes[first], self.nodes[second]
        if a[:3] != ('icmp', 'eq', 'i32') or b[:3] != ('icmp', 'ne', 'i32'):
            return False
        for subject, bounded in (a[3], tuple(reversed(a[3]))):
            for same, sentinel in (b[3], tuple(reversed(b[3]))):
                value = self.nodes[sentinel]
                if subject == same and value[0] == 'i32' and isinstance(value[1], int):
                    low, high = self.unsigned_bounds(bounded)
                    if value[1] < low or value[1] > high:
                        return True
        return False

    def phi_cases(self, value, guard, cases):
        """Flatten only direct nested phi choices under their exact edge guards."""
        if guard == '0':
            return
        node = self.nodes[value]
        if node[0] == 'guarded_phi':
            for incoming, incoming_guard in node[2]:
                self.phi_cases(incoming, self.logic('and', guard, incoming_guard), cases)
        else:
            value = self.restrict_selects(value, guard)
            cases[value] = self.logic('or', cases.get(value, '0'), guard)

    def restrict_selects(self, value, guard, memo=None):
        """Resolve pure select values only when a phi edge proves the condition.

        No float arithmetic is reassociated. Nested choices select an arm only
        when the exact use guard proves it; derivative contexts are not rewritten.
        """
        if memo is None:
            memo = {}
        if value in memo:
            return memo[value]
        node = self.nodes[value]
        # 标量select也可规范化为guarded_phi；仅完整使用条件蕴含某一分支时替换值。
        # 不跨call改写导数/读取/副作用，不重排或移动浮点运算。
        if node[0] == 'guarded_phi':
            for incoming, edge in node[2]:
                if self.logic('and', guard, self.logic('xor', edge, '1')) == '0':
                    result = self.restrict_selects(incoming, guard, memo)
                    memo[value] = result
                    return result
            return value
        if node[0] in ('boolean_decision', 'call'):
            return value
        if node[0] == 'select':
            condition, yes, no = node[1]
            predicate = self.boolean(condition)
            if self.logic('and', guard, self.logic('xor', predicate, '1')) == '0':
                result = self.restrict_selects(yes, guard, memo)
                memo[value] = result
                return result
            if self.logic('and', guard, predicate) == '0':
                result = self.restrict_selects(no, guard, memo)
                memo[value] = result
                return result

        def rewrite(item):
            if isinstance(item, str) and item in self.nodes:
                return self.restrict_selects(item, guard, memo)
            if isinstance(item, tuple):
                return tuple(rewrite(x) for x in item)
            return item

        changed = (node[0],) + tuple(rewrite(x) for x in node[1:])
        if node[0] in ('fadd', 'fmul', 'add', 'mul', 'and', 'or', 'xor'):
            changed = changed[:3] + (tuple(sorted(changed[3])),)
        result = value if changed == node else (self.integer_add(changed[3]) if self.mesh and changed[:3]==('add',None,'i32') else self.intern(changed))
        memo[value] = result
        return result

    def restrict_vertex_value(self,value,guard,memo=None):
        """Select proven phi arms beneath read-only vertex/compute expressions.

        This only substitutes an incoming value when the exact use guard
        implies its phi edge. Wave/derivative calls are opaque, and arithmetic
        operations keep their original nesting and types.
        """
        if memo is None: memo={}
        if value in memo:return memo[value]
        node=self.nodes[value]
        if node[0]=='guarded_phi':
            for incoming,edge in node[2]:
                if self.logic('and',guard,self.logic('xor',edge,'1'))=='0':
                    result=self.restrict_vertex_value(incoming,guard,memo)
                    memo[value]=result
                    return result
            return value
        if node[0]=='call':
            allowed={'legacyF16ToF32','rawBufferLoad.i32','rawBufferLoad.f32',
                     'cbufferLoadLegacy.i32','cbufferLoadLegacy.f32','binary.i32','binary.f32',
                     'tertiary.f32','dot2.f32','dot3.f32','dot4.f32'}
            if node[2] not in allowed or node[3] is not None:return value
        if node[0]=='boolean_decision':return value
        def rewrite(item):
            if isinstance(item,str) and item in self.nodes:
                return self.restrict_vertex_value(item,guard,memo)
            if isinstance(item,tuple):return tuple(rewrite(x) for x in item)
            return item
        changed=(node[0],)+tuple(rewrite(x) for x in node[1:])
        if node[0] in ('fadd','fmul','add','mul','and','or','xor'):
            changed=changed[:3]+(tuple(sorted(changed[3])),)
        result=value if changed==node else (self.integer_add(changed[3]) if self.mesh and changed[:3]==('add',None,'i32') else self.intern(changed))
        memo[value]=result
        return result

    def edge_guard(self, source, target):
        term = self.blocks[source][-1]
        if term.startswith('br label '):
            return self.reach(source)
        m = re.fullmatch(r'br i1 ([^,]+), label %([\w.]+), label %([\w.]+)', term)
        if m is None:
            raise ReconstructionError('未知分支条件。')
        condition = self.boolean(self.value(m[1], 'i1'))
        if m[2] == m[3]:
            return self.reach(source)
        if target == m[3]:
            condition = self.logic('xor', condition, '1')
        return self.logic('and', self.reach(source), condition)

    def reach(self, label):
        if label not in self.reach_cache:
            guard = '1' if label == '0' else '0'
            for pred in self.predecessors[label]:
                guard = self.logic('or', guard, self.edge_guard(pred, label))
            self.reach_cache[label] = guard
        return self.reach_cache[label]

    def killed_entry(self, block):
        """Exact path guard for lanes discarded before this acyclic block."""
        if block not in self.kill_entry_cache:
            killed = '0'
            for pred in self.predecessors[block]:
                killed = self.logic('or', killed,
                                    self.logic('and', self.killed_exit(pred), self.edge_guard(pred, block)))
            if self.loop and not self.compute and block == self.loop['header']:
                prior = self.bdd(self.intern(('loop_prior_discard',)))
                killed = self.logic('or', killed, self.logic('and', self.reach(block), prior))
            self.kill_entry_cache[block] = killed
        return self.kill_entry_cache[block]

    def discard_guard(self, body, block):
        match = re.fullmatch(r'call void @dx.op.discard\(i32 82, i1 (.+)\)', body)
        if match is None:
            raise ReconstructionError('未知 discard 调用形式。')
        return self.logic('and', self.reach(block), self.boolean(self.value(match[1], 'i1')))

    def killed_exit(self, block):
        if block not in self.kill_exit_cache:
            killed = self.killed_entry(block)
            for line in self.blocks[block][:-1]:
                if '@dx.op.discard(' in line:
                    killed = self.logic('or', killed, self.discard_guard(line, block))
            self.kill_exit_cache[block] = killed
        return self.kill_exit_cache[block]

    def call(self, body, block, position=None):
        call = re.fullmatch(r'call (\S+) @dx\.op\.([\w.]+)\((.*)\)', body)
        if not call:
            raise ReconstructionError('校验不支持此调用：' + body[:100])
        name = call[2]
        base = name.split('.')[0]
        if self.mesh and base in ('setMeshOutputCounts','emitIndices','storeVertexOutput','storePrimitiveOutput'):
            from .mesh import mesh_output_call
            output=mesh_output_call(body,self.mesh)
            return self.intern(('call','void',name,('mesh_event',self.compute_event_identity(block,position)),tuple(self.typed(arg) for arg in output['args'])))
        pure = {'createHandle', 'createHandleFromBinding', 'createHandleFromHeap', 'annotateHandle', 'loadInput',
                'cbufferLoadLegacy', 'textureLoad', 'sampleGrad', 'sampleLevel',
                'rawBufferLoad', 'unary', 'binary', 'tertiary', 'dot2', 'dot3', 'dot4', 'legacyF16ToF32', 'legacyF32ToF16', 'isSpecialFloat', 'sampleIndex'}
        if self.compute or self.mesh:pure |= {'threadId','groupId','threadIdInGroup','flattenedThreadIdInGroup'}
        wave = base in ('waveReadLaneFirst', 'waveAllOp', 'quadOp', 'waveActiveOp','waveAllTrue','waveActiveBit','wavePrefixOp')
        if base not in pure | {'sample', 'storeOutput', 'discard', 'atomicBinOp', 'rawBufferStore', 'waveReadLaneFirst', 'waveAllOp', 'quadOp', 'waveActiveOp','waveAllTrue','waveActiveBit','wavePrefixOp'} | ({'barrier'} if self.compute else set()):
            raise ReconstructionError('未知调用副作用，拒绝校验：' + name)
        args = split_operands(call[3])
        if base=='barrier':
            if call[1]!='void' or args!=['i32 80','i32 9'] or (block,position) not in self.shared_barriers:
                raise ReconstructionError('未知计算barrier类型或模式。')
        forms = {
            'legacyF16ToF32': ('float', ['i32 131','i32 ']),
            'legacyF32ToF16': ('i32', ['i32 130','float ']),
            'isSpecialFloat.f32': ('i1', ['i32 10','float ']),
            'sampleIndex.i32': ('i32', ['i32 90']),
            'unary.f16': ('half', ['i32 7','half ']),
            'binary.f16': ('half', ['i32 35','half ','half ']),
        }
        if self.compute or self.mesh:
            forms.update({'threadId.i32':('i32',['i32 93','i32 ']),'groupId.i32':('i32',['i32 94','i32 ']),
                          'threadIdInGroup.i32':('i32',['i32 95','i32 ']),'flattenedThreadIdInGroup.i32':('i32',['i32 96'])})
        if name in forms:
            if self.mesh and name=='threadId.i32':raise ReconstructionError('网格暂不支持DispatchThreadID。')
            ret, expected = forms[name]
            if call[1] != ret or len(args) != len(expected) or args[0] != expected[0] or any(not arg.startswith(prefix) for arg,prefix in zip(args[1:],expected[1:])):
                raise ReconstructionError('未知可见性调用类型、操作码或参数。')
            if name in ('threadId.i32','groupId.i32','threadIdInGroup.i32') and args[1] not in ('i32 0','i32 1','i32 2'):
                raise ReconstructionError('计算线程ID分量越界。')
        elif base in ('legacyF16ToF32','legacyF32ToF16','isSpecialFloat','sampleIndex','threadId','groupId','threadIdInGroup','flattenedThreadIdInGroup'):
            raise ReconstructionError('未知可见性调用。')
        memory_context = None
        if (self.compute or self.mesh) and (block,position) in self.compute_events:
            memory_context=('compute_event',self.compute_event_identity(block,position))
            if base=='rawBufferLoad' and self.compute_read_resource(strip_type(args[1]))[0]==0:
                resource=self.compute_read_resource(strip_type(args[1]));values=[strip_type(arg) for arg in args]
                properties=re.fullmatch(r'i32 12, i32 0, !(\d+)',resource[4])
                stride=re.fullmatch(r'i32 1, i32 (\d+)',resource[5].get(properties[1],'')) if properties else None
                width={'1':1,'3':2,'7':3,'15':4}.get(values[4]) if len(values)==6 else None
                raw=(resource[3]=='%struct.ByteAddressBuffer' and resource[4]=='i32 11, i32 0, null'
                     and name=='rawBufferLoad.i32' and values[3]=='undef') if len(values)==6 else False
                structured=(bool(re.fullmatch(r'%"(?:hostlayout\.)?class.StructuredBuffer<[^"\n]+>"',resource[3]))
                            and stride and values[3].isdigit() and int(values[3])%4==0 and width
                            and int(values[3])+width*4<=int(stride[1])) if len(values)==6 else False
                if not (name in ('rawBufferLoad.i32','rawBufferLoad.f32') and call[1]=='%dx.types.ResRet.'+name.rsplit('.',1)[1]
                        and len(args)==6 and args[0]=='i32 139' and all(arg.startswith('i32 ') for arg in args[2:4]+args[5:6])
                        and args[4].startswith('i8 ') and width and values[5]=='4' and (raw or structured)):
                    raise ReconstructionError('计算SRV读取类型、布局、偏移或对齐不受支持。')
            if base=='rawBufferLoad' and self.compute_read_resource(strip_type(args[1]))[0]==1:
                resource=self.static_handle(strip_type(args[1]))
                values=[strip_type(arg) for arg in args]
                properties=re.fullmatch(r'i32 12, i1 false, i1 false, i1 false, !(\d+)',resource[4])
                stride=re.fullmatch(r'i32 1, i32 (\d+)',resource[5].get(properties[1],'')) if properties else None
                width={'1':1,'3':2,'7':3,'15':4}.get(values[4]) if len(values)==6 else None
                raw=(resource[3]=='%struct.RWByteAddressBuffer' and resource[4]=='i32 11, i1 false, i1 false, i1 false, null'
                     and name=='rawBufferLoad.i32' and values[3]=='undef') if len(values)==6 else False
                structured=(bool(re.fullmatch(r'%"(?:hostlayout\.)?class.RWStructuredBuffer<[^"\n]+>"',resource[3]))
                            and stride and values[3].isdigit() and int(values[3])%4==0 and width
                            and int(values[3])+width*4<=int(stride[1])) if len(values)==6 else False
                if not (name in ('rawBufferLoad.i32','rawBufferLoad.f32') and call[1]=='%dx.types.ResRet.'+name.rsplit('.',1)[1]
                        and len(args)==6 and args[0]=='i32 139' and all(arg.startswith('i32 ') for arg in args[2:4]+args[5:6])
                        and args[4].startswith('i8 ') and width and values[5]=='4' and (raw or structured)):
                    raise ReconstructionError('计算UAV读取类型、布局、偏移或对齐不受支持。')
        if (self.ordered_memory or self.compute) and base in ('atomicBinOp', 'rawBufferStore'):
            if position is None:
                raise ReconstructionError('有序内存缺少指令位置。')
            if len(args) != (7 if base == 'atomicBinOp' else 10):
                raise ReconstructionError('有序内存参数数量不符。')
            resource = self.static_handle(strip_type(args[1]))
            if resource[0] != 1:
                raise ReconstructionError('可写内存需要UAV绑定。')
            if base == 'atomicBinOp':
                if self.compute:
                    values=[strip_type(arg) for arg in args]
                    raw=(name=='atomicBinOp.i32' and call[1]=='i32' and resource[3]=='%struct.RWByteAddressBuffer'
                         and resource[4]=='i32 11, i1 false, i1 false, i1 false, null' and args[4:6]==['i32 undef','i32 undef'])
                    texture_props=re.fullmatch(r'i32 2, i1 false, i1 false, i1 false, !(\d+)',resource[4])
                    texture=(name=='atomicBinOp.i32' and call[1]=='i32' and resource[3]=='%"class.RWTexture2D<unsigned int>"'
                             and texture_props and resource[5].get(texture_props[1])=='i32 0, i32 5' and args[5]=='i32 undef')
                    wide_props=re.fullmatch(r'i32 12, i1 false, i1 false, i1 false, !(\d+)',resource[4])
                    wide=(name=='atomicBinOp.i64' and call[1]=='i64' and resource[3]=='%"class.RWStructuredBuffer<unsigned long long>"'
                          and wide_props and resource[5].get(wide_props[1])=='i32 1, i32 8, i32 3, i32 1'
                          and re.search(r'^'+re.escape(resource[3])+r' = type \{ i64 \}$',self.ir,re.M)
                          and args[2]=='i32 7' and args[4:6]==['i32 0','i32 undef'])
                    if not (args[0]=='i32 78' and args[2] in ('i32 0','i32 1','i32 2','i32 3','i32 6','i32 7','i32 8')
                            and all(arg.startswith('i32 ') for arg in args[2:6])
                            and args[6].startswith('i64 ' if wide else 'i32 ') and (raw or texture or wide)):
                        raise ReconstructionError('计算原子类型、操作码、绑定、偏移或布局不符。')
                else:
                    if not (name == 'atomicBinOp.i32' and call[1] == 'i32' and len(args) == 7 and args[0] == 'i32 78'
                        and args[2] in ('i32 0','i32 1','i32 2','i32 3','i32 6','i32 7')
                        and all(arg.startswith('i32 ') for arg in args[2:])
                        and args[4:6] == ['i32 undef','i32 undef']
                        and resource[3] == '%struct.RWByteAddressBuffer'
                        and resource[4] == 'i32 11, i1 false, i1 false, i1 false, null'):
                        raise ReconstructionError('有序内存不支持此原子类型、操作码或资源属性。')
                    memory_context = ('ordered_memory_ordinal', self.memory_positions[block].index(position))
            else:
                props = re.fullmatch(r'i32 12, i1 false, i1 false, i1 false, !(\d+)', resource[4])
                stride = re.fullmatch(r'i32 1, i32 (\d+)', resource[5].get(props[1], '')) if props else None
                values = [strip_type(arg) for arg in args]
                width = {'1':1,'3':2,'7':3,'15':4}.get(values[8]) if len(values) == 10 else None
                raw_compute = self.compute and resource[3]=='%struct.RWByteAddressBuffer' and resource[4]=='i32 11, i1 false, i1 false, i1 false, null'
                if not (name in ('rawBufferStore.i32','rawBufferStore.f32') and call[1] == 'void'
                        and len(args) == 10 and args[0] == 'i32 140' and width and values[9] == '4'
                        and all(arg.startswith('i32 ') for arg in args[2:4] + args[9:10])
                        and args[8].startswith('i8 ')
                        and all(arg.startswith(('float ' if name.endswith('.f32') else 'i32 ')) for arg in args[4:8])
                        and (raw_compute and name=='rawBufferStore.i32' and values[3]=='undef'
                             or re.fullmatch(r'%"class.RWStructuredBuffer<[^"\n]+>"', resource[3])
                             and stride and values[3].isdigit() and int(values[3]) % 4 == 0
                             and int(values[3]) + width*4 <= int(stride[1]))
                        and all(v == 'undef' for v in values[4+width:8])):
                    raise ReconstructionError('有序内存不支持此写入形状、步长或越界通道。')
        elif base == 'rawBufferStore':
            raise ReconstructionError('可写内存尚未建立顺序模型。')
        implicit_sample = base == 'sample'
        if implicit_sample and not (name == 'sample.f32' and call[1] == '%dx.types.ResRet.f32'
                                    and len(args) == 11 and args[0] == 'i32 60'
                                    and all(arg.startswith('%dx.types.Handle ') for arg in args[1:3])
                                    and all(arg.startswith('float ') for arg in args[3:7] + args[10:11])
                                    and all(arg.startswith('i32 ') for arg in args[7:10])):
            raise ReconstructionError('未知隐式采样操作码、类型或参数，拒绝校验。')
        if wave and not (call[1] == 'i32' and len(args) == 2 and (
                name == 'waveReadLaneFirst.i32' and args[0] == 'i32 118' and args[1].startswith('i32 ') or
                name == 'waveAllOp' and args[0] == 'i32 135' and args[1].startswith('i1 '))
                or call[1] == 'i32' and name == 'quadOp.i32' and len(args) == 3 and args[0] == 'i32 123' and args[1].startswith('i32 ') and args[2] in ('i8 0','i8 1','i8 2')
                or self.compute and call[1]=='i1' and name=='waveAllTrue' and len(args)==2 and args[0]=='i32 114' and args[1].startswith('i1 ')
                or self.compute and call[1]=='i32' and name=='waveActiveBit.i32' and len(args)==3 and args[0]=='i32 120' and args[1].startswith('i32 ') and args[2]=='i8 1'
                or self.compute and call[1]=='i32' and name=='wavePrefixOp' and len(args)==2 and args[0]=='i32 136' and args[1].startswith('i1 ')
                or call[1] == 'i32' and name == 'waveActiveOp.i32' and len(args) == 4 and args[0] == 'i32 119' and args[1].startswith('i32 ') and args[2:] == ['i8 2','i8 1']):
            raise ReconstructionError('未知 wave 操作码或类型，拒绝校验。')
        # Derivatives and implicit-LOD samples depend on the quad execution guard.
        # Wave intrinsics likewise depend on the exact executing-lane guard.
        context = self.reach(block) if wave or implicit_sample or base == 'unary' and args[0] in ('i32 83', 'i32 84', 'i32 85', 'i32 86') else memory_context
        if wave and self.nested_loops:
            context=('nested_wave_occurrence',memory_context,context)
        return self.intern(('call', call[1], name, context, tuple(self.typed(arg) for arg in args)))

    def expression(self, name):
        if name in self.cache:
            return self.cache[name]
        if name in self.local_loads:
            array,index = self.local_loads[name]
            result = self.intern(('private_uint4_load', tuple(self.value(self.local_arrays[array]['values'][i], 'i32') for i in range(4)), self.value(index,'i32')))
            self.cache[name] = result
            return result
        if name in self.loop_parameters:
            type, key = self.loop_parameters[name]
            result = self.intern(('loop_state_parameter', type, key))
            self.cache[name] = result
            return result
        if name not in self.defs:
            raise ReconstructionError('未知 SSA：' + name)
        block, raw = self.defs[name]
        if name in self.shared_loads:
            array,index=self.shared_loads[name]
            result=self.intern(('shared_load','float',self.shared_arrays[array]['name'],self.shared_arrays[array]['size'],
                                self.value(index,'i32'),('compute_event',self.compute_event_identity(block,self.definition_positions[name]))))
        elif name in self.shared_pointers:
            array,index=self.shared_pointers[name]
            result=self.intern(('shared_pointer',self.shared_arrays[array]['name'],self.value(index,'i32')))
        elif raw.startswith('call '):
            result = self.call(raw, block, self.definition_positions[name])
        elif raw.startswith('phi '):
            type, incoming = raw[4:].split(' ', 1)
            edges = re.findall(r'\[ ([^,]+), %([\w.]+) \]', incoming)
            cases = {}
            for value, pred in edges:
                value = self.value(value, type)
                guard = self.edge_guard(pred, block)
                if self.compute or self.mesh:value=self.restrict_vertex_value(value,guard)
                self.phi_cases(value, guard, cases)
            result = next(iter(cases)) if len(cases) == 1 else self.intern(('guarded_phi', type, tuple(sorted(cases.items()))))
        elif raw.startswith('extractvalue '):
            m = re.fullmatch(r'extractvalue (\S+) (%[\w.]+), (\d+)', raw)
            if not m:
                raise ReconstructionError('未知聚合提取。')
            result = self.structured_scalar(m[2], int(m[3]))
            if result is None:
                result = self.intern(('extractvalue', m[1], self.expression(m[2]), int(m[3])))
        elif raw.startswith('select '):
            operands = split_operands(raw[7:])
            arguments=tuple(self.typed(x) for x in operands)
            scalar_type = operands[1].split(' ', 1)[0]
            acyclic_pixel = not (self.vertex or self.compute or self.mesh or self.loop)
            if self.mesh or acyclic_pixel and scalar_type in ('float', 'half', 'i32'):
                # LLVM select and an acyclic phi express the same scalar
                # choice. Retain both exact predicate arms, including undef;
                # enclosing phi edges intersect these guards in phi_cases.
                condition=self.boolean(arguments[0]);cases={}
                self.phi_cases(arguments[1],condition,cases)
                self.phi_cases(arguments[2],self.logic('xor',condition,'1'),cases)
                result=next(iter(cases)) if len(cases)==1 else self.intern(('guarded_phi',scalar_type,tuple(sorted(cases.items()))))
            else:result = self.intern(('select',arguments))
        elif raw.startswith('getelementptr '):
            m = re.fullmatch(r'getelementptr inbounds \[(\d+) x float\], \[\1 x float\]\* @(\w+), i32 0, i32 (.+)', raw)
            if not m or m[2] not in self.constants or self.constants[m[2]][0] != int(m[1]):
                raise ReconstructionError('未知静态数组地址。')
            result = self.intern(('const_array_pointer', self.constants[m[2]], self.value(m[3], 'i32')))
        elif raw.startswith('load '):
            m = re.fullmatch(r'load float, float\* (%[\w.]+), align 4(?:, !(?:tbaa|noalias|alias.scope) !\d+)*', raw)
            if not m:
                raise ReconstructionError('未知内存读取。')
            pointer = self.expression(m[1])
            if self.nodes[pointer][0] != 'const_array_pointer':
                raise ReconstructionError('非静态数组内存读取。')
            result = self.intern(('const_array_load', pointer))
        else:
            cast = re.fullmatch(r'(bitcast|fptosi|fptoui|sitofp|uitofp|fptrunc|fpext|zext) (.+) to (\S+)', raw)
            if cast:
                if cast[1] == 'bitcast' and cast[2].split(' ', 1)[0] in ('float', 'i32') and cast[3] in ('float', 'i32'):
                    result = self.bitcast32(self.typed(cast[2]), cast[3])
                else:
                    operand = self.typed(cast[2])
                    limited = self.half_limited_intrinsic(operand) if cast[1] == 'fptrunc' and cast[3] == 'half' and cast[2].startswith('float ') else None
                    result = limited if limited is not None else self.intern((cast[1], operand, cast[3]))
            else:
                operation = re.fullmatch(r'(\w+)(?: (?:fast|nsw|nuw|exact))* (?:(\w+) )?(float|half|i32|i64|i1) (.+)', raw)
                if not operation:
                    raise ReconstructionError('未知定义：' + raw[:140])
                op, pred, type, operands = operation.groups()
                args = [self.value(x, type) for x in split_operands(operands)]
                if op == 'fcmp' and pred == 'ogt' and type == 'half' and self.nodes[args[1]] == ('float16_bits','0000'):
                    saturated = self.nodes[args[0]]
                    if saturated[:3] == ('call','half','unary.f16') and self.nodes[saturated[4][0]] == ('i32',7):
                        args[0] = saturated[4][1]
                if op == 'add' and type == 'i32':
                    result = self.integer_add(args)
                    self.cache[name] = result
                    self.origins.setdefault(result, []).append(name + ' = ' + raw)
                    return result
                if op in ('fadd', 'fmul', 'add', 'mul', 'and', 'or', 'xor') or op in ('icmp', 'fcmp') and pred in ('eq', 'ne', 'oeq', 'one', 'ueq', 'une'):
                    args.sort()
                if op in ('icmp', 'fcmp') and pred and pred[-2:] in ('gt', 'ge'):
                    pred = pred[:-2] + {'gt': 'lt', 'ge': 'le'}[pred[-2:]]
                    args.reverse()
                if self.nested_loops and op=='shl' and type=='i32' and self.nodes[args[1]][0]=='i32' and isinstance(self.nodes[args[1]][1],int) and self.nodes[args[1]][1]<32:
                    from .nested_compute import rewrite_unobserved_high_bits
                    args[0]=rewrite_unobserved_high_bits(self,args[0],32-self.nodes[args[1]][1])
                result = self.intern((op, pred, type, tuple(args)))
        self.cache[name] = result
        self.origins.setdefault(result, []).append(name + ' = ' + raw)
        return result

    def observable_blocks(self):
        blocks = []
        for label in self.order:
            effects = []
            for position, line in enumerate(self.blocks[label][:-1]):
                if line.startswith('call void '):
                    effects.append(self.call(line, label, position))
            term = self.blocks[label][-1]
            if term == 'ret void':
                terminator = ('return',)
            elif term.startswith('br label '):
                terminator = ('jump', self.labels[term[10:]])
            else:
                branch = re.fullmatch(r'br i1 ([^,]+), label %([\w.]+), label %([\w.]+)', term)
                if not branch:
                    raise ReconstructionError('未知终结指令。')
                terminator = ('branch', self.value(branch[1], 'i1'), self.labels[branch[2]], self.labels[branch[3]])
            blocks.append({'effects': effects, 'terminator': terminator})
        return blocks

    def compute_event(self,line,block,position):
        if (block,position) in self.shared_stores:
            array,index,value=self.shared_stores[(block,position)];declaration=self.shared_arrays[array]
            return self.intern(('shared_store','float',declaration['name'],declaration['size'],self.value(index,'i32'),self.value(value,'float')))
        name=line.split(' = ',1)[0]
        if name in self.shared_loads:return self.expression(name)
        return self.call(line.split(' = ',1)[-1],block,position)

    def observables(self):
        effects, derivative_calls, output_slots = [], set(), set()
        vertex_outputs = {}
        effect_masks, derivative_masks = [], set()
        wave_calls, wave_masks = set(), set()
        ordered_memory_blocks = []
        discarded = '0'
        returned = '0'
        for block, lines in self.blocks.items():
            killed = self.killed_entry(block)
            memory_events = []
            for position, line in enumerate(lines[:-1]):
                if (block,position) in self.local_stores or (block,position) in self.local_ignored:
                    continue
                body = line.split(' = ', 1)[-1]
                if self.compute and (block,position) in self.shared_stores:
                    memory_events.append((self.compute_event(line,block,position),killed))
                    continue
                if self.compute and line.split(' = ',1)[0] in self.shared_loads:
                    memory_events.append((self.compute_event(line,block,position),killed))
                    continue
                if body.startswith('call '):
                    call = self.call(body, block, position)  # Reject unknown effects, including unused results.
                    node = self.nodes[call]
                    if self.mesh and node[0]=='guarded_phi':
                        # Only the validated pure half conversion can return
                        # a scalar choice instead of a call node.
                        continue
                    if (self.compute or self.mesh) and (block,position) in self.compute_events:
                        memory_events.append((call,killed))
                        if node[2] in ('waveReadLaneFirst.i32','waveAllOp','waveActiveOp.i32','waveAllTrue','waveActiveBit.i32','wavePrefixOp'):
                            wave_calls.add(call);wave_masks.add((call,killed))
                        continue
                    if node[2] == 'discard':
                        guard = self.discard_guard(body, block)
                        discarded = self.logic('or', discarded, guard)
                        killed = self.logic('or', killed, guard)
                        continue
                    if body.startswith('call void ') or node[2].startswith('atomicBinOp.'):
                        if self.ordered_memory and node[2].split('.')[0] in ('atomicBinOp','rawBufferStore'):
                            memory_events.append((call, killed))
                            continue
                        if node[2].startswith('storeOutput.'):
                            if self.vertex:
                                if self.loop or killed != '0':
                                    raise ReconstructionError('顶点分支输出暂不支持循环或discard。')
                                key = (node[2],node[4][:4])
                                cases,coverage = vertex_outputs.get(key,({},'0'))
                                guard = self.reach(block)
                                if self.logic('and',coverage,guard) != '0':
                                    raise ReconstructionError('同一路径上的顶点输出重写尚未建模。')
                                self.phi_cases(node[4][4],guard,cases)
                                vertex_outputs[key] = cases,self.logic('or',coverage,guard)
                                continue
                            # Multiple writes to a slot require ordered memory modeling.
                            slot = node[4][1:4]
                            if slot in output_slots:
                                raise ReconstructionError('重复输出写入不在校验范围。')
                            output_slots.add(slot)
                        effects.append((self.reach(block), call))
                        effect_masks.append((self.reach(block), call, killed))
                    elif node[2] in ('waveReadLaneFirst.i32', 'waveAllOp', 'quadOp.i32', 'waveActiveOp.i32','waveAllTrue'):
                        wave_calls.add(call)
                        wave_masks.add((call, killed))
                    elif node[3] is not None:
                        derivative_calls.add(call)
                        derivative_masks.add((call, killed))
                elif ' = ' not in line:
                    raise ReconstructionError('未知潜在副作用：' + line)
            if memory_events:
                ordered_memory_blocks.append((self.reach(block), tuple(memory_events)))
            if lines[-1] == 'ret void':
                returned = self.logic('or', returned, self.reach(block))
        if discarded != '0':
            discard = self.intern(('call', 'void', 'discard', None,
                                   (self.value('82', 'i32'), self.value('true', 'i1'))))
            effects.append((discarded, discard))
        vertex_result = {}
        for (name,arguments),(cases,coverage) in vertex_outputs.items():
            scalar_type = {'storeOutput.f32':'float','storeOutput.i32':'i32'}.get(name)
            if scalar_type is None:
                raise ReconstructionError('未知顶点输出类型。')
            # An output not assigned on a returning path is undefined, just
            # like DXC's phi with an undef incoming edge. Preserve that guard.
            missing = self.logic('and',returned,self.logic('xor',coverage,'1'))
            self.phi_cases(self.value('undef',scalar_type),missing,cases)
            vertex_result[name+':'+','.join(arguments)] = tuple(sorted(cases.items()))
        # Identical event sequences in mutually exclusive arms may be duplicated
        # by structured source emission. Merge only their exact BDD guards.
        memory_sequences = {}
        for guard, events in ordered_memory_blocks:
            memory_sequences[events] = self.logic('or', memory_sequences.get(events, '0'), guard)
        result = {'effects': sorted(effects), 'derivatives': sorted(derivative_calls),
                'effect_discard_masks': sorted(effect_masks),
                'derivative_discard_masks': sorted(derivative_masks),
                'wave_operations': sorted(wave_calls), 'wave_discard_masks': sorted(wave_masks),
                'ordered_memory_blocks': sorted((guard, events) for events, guard in memory_sequences.items()),
                'return_guard': returned}
        if self.compute or self.mesh:
            result['compute_threads']=self.compute_threads
            # Preserve complete per-invocation event order across an acyclic
            # CFG. Bounded expansion fails closed instead of dropping paths.
            by_block={block:tuple((self.compute_event(line,block,pos),'0')
                                  for pos,line in enumerate(lines[:-1]) if (block,pos) in self.compute_events)
                      for block,lines in self.blocks.items()}
            paths={}
            visits=0
            def visit(block,guard,events):
                nonlocal visits
                visits+=1
                events=events+by_block[block]
                for loop in self.nested_loops:
                    if block==loop['header']:
                        # Boundary markers distinguish one abstract iteration
                        # from child-loop entry and from function suffix effects.
                        events+=((('nested_loop_enter',loop['key']),'0'),)
                if len(events)>128 or len(paths)>512 or visits>16384:
                    raise ReconstructionError(f'计算有序路径超出受限模型：events={len(events)}, paths={len(paths)}, visits={visits}。')
                term=self.blocks[block][-1]
                if term=='ret void':
                    if self.nested_loops:
                        sink=next((loop for loop in self.nested_loops if block==loop['backedge_sink']),None)
                        events+=((('nested_loop_continue',sink['key']) if sink else ('nested_function_return',),'0'),)
                    if self.loop:
                        events+=((('loop_continue' if block==self.loop['backedge_sink'] else 'function_return'),'0'),)
                    if events not in paths and len(paths)>=512:
                        raise ReconstructionError('计算有序路径超过512条独立事件序列。')
                    paths[events]=self.logic('or',paths.get(events,'0'),guard)
                    return
                for child in dict.fromkeys(re.findall(r'label %([\w.]+)',term)):
                    next_guard=self.logic('and',guard,self.edge_guard(block,child))
                    edge_events=events
                    for loop in self.nested_loops:
                        if block==loop['latch'] and child==loop['exit']:
                            edge_events+=((('nested_loop_exit',loop['key']),'0'),)
                    if next_guard!='0':visit(child,next_guard,edge_events)
            if self.nested_loops or self.mesh:
                from .nested_compute import nested_event_paths
                paths=nested_event_paths(self,by_block)
            else:
                visit('0','1',())
            result['ordered_memory_blocks']=sorted((guard,events) for events,guard in paths.items())
            if self.shared_arrays:
                result['shared_layouts']=sorted((a['name'],a['type'],a['size']) for a in self.shared_arrays.values())
            if self.nested_loops:
                from .nested_compute import nested_observables
                result['nested_compute_loops']=nested_observables(self)
            if self.mesh:
                result['mesh_interface']={k:v for k,v in self.mesh.items() if k not in ('entry','body','profile')}
        if self.vertex:
            result['vertex_outputs'] = vertex_result
        if self.loop:
            loop = self.loop
            result.update({
                'loop_initial_values': sorted((s['key'], s['type'], self.value(s['initial'], s['type'])) for s in self.loop_states),
                'loop_next_values': sorted((s['key'], s['type'], self.value(s['feedback'], s['type'])) for s in self.loop_states),
                'loop_entry_guard': self.edge_guard(loop['preheader'], loop['header']),
                'loop_continue_guard': self.edge_guard(loop['latch'], loop['backedge_sink']),
                'loop_exit_guard': self.edge_guard(loop['latch'], loop['exit']),
                'loop_initial_discard_mask': self.logic('and', self.reach(loop['header']), self.killed_exit(loop['preheader'])),
                'loop_next_discard_mask': self.killed_exit(loop['latch']),
            })
        return result


def compare_dataflow(left_ir, right_ir):
    from .control_flow import NestedLoopRequired
    try:
        left, right = Graph(left_ir), Graph(right_ir)
    except NestedLoopRequired:
        from .nested_compute import compare_nested_compute_dataflow
        return compare_nested_compute_dataflow(left_ir,right_ir)
    a, b = left.observables(), right.observables()
    compute_bijections_checked=0
    if left.compute and right.compute and left.loop and right.loop and len(left.loop_states)==len(right.loop_states):
        from itertools import permutations
        expected_types=[s['type'] for s in left.loop_states]
        for mapping in permutations(range(len(right.loop_states))):
            if any(s['type']!=expected_types[key] for s,key in zip(right.loop_states,mapping)):continue
            compute_bijections_checked+=1
            if mapping!=tuple(range(len(mapping))):
                right=Graph(right_ir,compute_state_keys=mapping);b=right.observables()
            if a==b:break
    refined_outputs = False
    if left.vertex and right.vertex:
        expected, actual = a['vertex_outputs'], b['vertex_outputs']
        # DXIL undef in an unwritten output lane allows DXC to choose any
        # value on exactly that path. This is directional refinement: never
        # permit undef to replace a source-defined value. Other observables
        # still compare normally. Shared interned BDDs retain exact guards.
        left.nodes.update(right.nodes)
        left.bdd_nodes.update(right.bdd_nodes)
        compatible = expected.keys() == actual.keys()
        for slot,cases in expected.items():
            candidates = dict(actual.get(slot,()))
            for value,guard in cases:
                node = left.nodes[value]
                if node in (('float','undef'),('i32','undef')):
                    refined_outputs = True
                    continue
                matched = '0'
                for candidate,candidate_guard in candidates.items():
                    overlap=left.logic('and',guard,candidate_guard)
                    if overlap!='0' and left.restrict_vertex_value(value,overlap)==left.restrict_vertex_value(candidate,overlap):
                        matched=left.logic('or',matched,overlap)
                if left.logic('and',guard,left.logic('xor',matched,'1')) != '0':
                    compatible = False
        if compatible:
            a.pop('vertex_outputs'); b.pop('vertex_outputs')
    differences = [{'kind': key, 'left': a.get(key), 'right': b.get(key)}
                   for key in sorted(a.keys() | b.keys()) if a.get(key) != b.get(key)]
    return {'equal': not differences, 'blocks': [len(left.blocks), len(right.blocks)],
            'ssa_definitions': [len(left.defs), len(right.defs)],
            'wave_operation_counts': [len(a['wave_operations']), len(b['wave_operations'])],
            'loop_state_counts': [len(left.loop_states), len(right.loop_states)],
            'compute_state_bijections_checked':compute_bijections_checked,
            'compute_loop_state_mapping':([
                {'source':left.loop_states[s['key'][1]]['name'],'candidate':s['name'],'type':s['type']}
                for s in right.loop_states] if not differences and compute_bijections_checked else None),
            'compute_event_path_counts':([len(a['ordered_memory_blocks']),len(b['ordered_memory_blocks'])] if left.compute and right.compute else None),
            'source_undefined_vertex_outputs_may_be_refined': refined_outputs,
            'differences': differences,
            'rules': ['SSA renaming and duplicate pure definitions ignored',
                      'Commutative binary operands and phi edge order normalized',
                      'i32 bitwise and/or/xor reassociate exactly; unsigned remainder and legacy f32-to-f16 packing preserve operands, types and opcode',
                      'Integer addition reassociated modulo 2^32; float operations are not reassociated',
                      'Integer literals normalized modulo bit width; float32 bit patterns compared',
                      '32-bit bitcasts commute through selects and preserve exact constant bits',
                      'Masked unsigned equality implies inequality with an out-of-range sentinel',
                      'Optimizer flags ignored; exceptional floating-point/undefined behavior not proven',
                      'Phi values use exact reduced Boolean decision diagrams of incoming path guards',
                      'Acyclic pixel scalar float/half/i32 selects and phi choices retain identical predicate arms; derivative/read/effect execution remains separate',
                      'Direct nested phi choices flatten and comparisons distribute over exact incoming path guards',
                      'Pure select values on phi edges simplify only when the exact edge guard proves their condition',
                      'Read-only structured load lanes compare exact element index, byte offset, scalar type and declared stride; status/OOB lanes rejected',
                      'Output/discard effects and derivative execution guards compared; empty CFG edge blocks may differ',
                      'Implicit float sample opcode60 preserves resource, sampler, coordinates, offsets, clamp, execution guard and prior-discard mask; unknown forms rejected',
                      'Discard guards combine idempotently; prior discard masks for every output, atomic and derivative remain exact',
                      'WaveReadLaneFirst.i32, WaveActiveCountBits, quad x/y/diagonal reads, unsigned WaveActiveMin and CS WaveActiveAllTrue preserve exact supported forms, arguments, execution guards and prior-discard masks; unknown wave operations rejected',
                      'One natural output-state/pending loop compares initial values, an arbitrary-state transition, exit outputs and carried discard masks; loops with writes or unmatched states rejected',
                      'A single read-only CS natural loop compares a typed state bijection, initial values, arbitrary-state feedback, entry/continue/exit guards, exact per-iteration raw SRV/UAV reads and suffix effects; writes before/inside, collectives inside and unmatched states are rejected; no termination or cross-thread memory proof',
                      'One reporting raw-buffer atomic preserves guarded effects; only verified static cbuffer or typed float texture reads may follow under legal binding disjointness; general aliasing reads and extra atomics rejected',
                      'Acyclic bounded UAV sequences preserve every event, execution guard and order; PS writes use one memory block per path; CS permits cross-block raw/structured UAV reads, raw writes and collectives, preserving fixed numthreads; only proven static cbuffer reads may follow writes',
                      'Acyclic dynamic raw SRV array/heap reads preserve descriptor index/nonuniform flag, mask, alignment, address and per-path event ordinal; ambiguous prefix lengths, dynamic UAVs, dynamic loop reads and post-write alias-sensitive reads are rejected',
                      'Half saturation and max with a positive finite normal half constant may use exact half->float intrinsic->half lowering; all rounding boundaries remain, and arbitrary float reassociation is rejected',
                      'Private uint[4] loads preserve every initialized slot and bounded index; lifetime enclosure checked; mutation, escape, partial initialization and loop combinations rejected',
                      'Read-only acyclic VS output stores require disjoint path guards; candidate outputs preserve every source-defined value/guard, while a directly undefined output lane may be refined only on its original path'],
            'limitation': 'Structural dataflow evidence for this subset; not a general IEEE, GPU, or performance equivalence proof.'}
