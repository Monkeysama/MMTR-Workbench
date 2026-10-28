"""受控材质 Shader 分片：固定 worker 数、精确缓存分组和完整覆盖校验。

子分片只返回单 Shader 证明与候选载荷，不持有或提交文档事务。父 worker 继续执行
原有全局 metadata/build/validate 收尾；任一分片失败时整组丢弃。
"""
import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from .material_process import document_state, material_worker_command
from .model import EditError


MAX_WORKERS = 4
SHARD_SOURCE_BYTES = 128 * 1024 * 1024
SHARD_TIMEOUT_SECONDS = 240
PAYLOAD_CACHE_BYTES = 64 * 1024 * 1024
COUNTER_KEYS = {'disassemble', 'assemble', 'validate', 'shader_cache_hits', 'analyzed_shaders', 'migration_calls'}
DURATION_KEYS = {'planning', 'analysis', 'declarations', 'migration', 'disassemble', 'assemble', 'validate'}


# 同一缓存键的 Shader 必须留在同一片；再按源字节做确定性贪心均衡。
def plan_shards(document, info, plan_digest, texture_shaders, *, workers=MAX_WORKERS):
    if type(workers) is not int or not 1 <= workers <= MAX_WORKERS:
        raise EditError('Shader 并行 worker 数超出受控范围。')
    groups = {}
    for shader in info['shaders']:
        index = shader['index']
        original = document.shader_bytes(index)
        key = (hashlib.sha256(original).digest(), shader['stage'], plan_digest, index in texture_shaders)
        groups.setdefault(key, []).append((index, len(original)))
    if not groups:
        return []
    all_indices = sorted(shader['index'] for shader in info['shaders'])
    # 每个分片都会重建完整文档；预算必须包含源容器及已有替换，而不只是分配的 Shader。
    base_bytes = len(document.original)+sum(len(payload) for payload in document.replacements.values())
    largest = max(sum(size for _, size in rows) for rows in groups.values())
    if base_bytes+largest > SHARD_SOURCE_BYTES:
        return [all_indices]
    count = min(workers, len(groups))
    bins = [{'indices': [], 'bytes': base_bytes} for _ in range(count)]
    ordered = sorted(groups.values(), key=lambda rows: (-sum(size for _, size in rows), rows[0][0]))
    for group in ordered:
        target = min(bins, key=lambda row: (row['bytes'], len(row['indices']), row['indices'][:1]))
        target['indices'].extend(index for index, _ in group)
        target['bytes'] += sum(size for _, size in group)
    result = []
    for row in bins:
        if not row['indices']:
            raise EditError('Shader 分片覆盖为空。')
        if row['bytes'] > SHARD_SOURCE_BYTES:
            # 单片超预算时退回串行协调，不在父 worker 内复制第二套半成功状态。
            return [all_indices]
        result.append(sorted(row['indices']))
    flattened = [index for shard in result for index in shard]
    expected = [shader['index'] for shader in info['shaders']]
    if len(flattened) != len(set(flattened)) or sorted(flattened) != sorted(expected):
        raise EditError('Shader 分片未精确覆盖全部 Shader。')
    return result


# 只解析受控 JSON 字段；载荷仍由 Document.replace_shader 和 validate_shader 二次校验。
def validate_pack(pack, assignment, keys_by_index, *, plan_digest, source_sha256, dxc_sha256):
    required = {'plan_digest', 'source_sha256', 'dxc_sha256', 'covered', 'replacements', 'reports',
                'registers', 'reflections', 'native_proofs', 'fixed_shaders', 'counters', 'durations', 'cache_bytes'}
    if not isinstance(pack, dict) or set(pack) != required:
        raise EditError('Shader 分片协议字段不完整。')
    if (pack['plan_digest'], pack['source_sha256'], pack['dxc_sha256']) != (plan_digest, source_sha256, dxc_sha256):
        raise EditError('Shader 分片计划、源或 DXC 身份不一致。')
    expected = sorted(assignment)
    covered = pack['covered']
    if (not isinstance(covered, list) or covered != expected
            or any(type(index) is not int for index in covered)):
        raise EditError('Shader 分片覆盖范围与分配不一致。')

    def index_map(value):
        if not isinstance(value, dict):
            raise EditError('Shader 分片映射字段类型错误。')
        result = {}
        for key, row in value.items():
            if not isinstance(key, str) or not key.isdigit():
                raise EditError('Shader 分片索引格式错误。')
            index = int(key)
            if index not in assignment or index in result:
                raise EditError('Shader 分片索引越界或重复。')
            result[index] = row
        return result

    replacements = index_map(pack['replacements'])
    registers = index_map(pack['registers'])
    reflections = index_map(pack['reflections'])
    native_proofs = index_map(pack['native_proofs'])
    if set(reflections) != assignment:
        raise EditError('Shader 分片反射覆盖不完整。')
    if set(registers) != set(replacements):
        raise EditError('Shader 分片寄存器与替换载荷不一致。')
    payloads = {}
    for index, encoded in replacements.items():
        if not isinstance(encoded, str):
            raise EditError('Shader 分片载荷类型错误。')
        try:
            payloads[index] = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise EditError('Shader 分片载荷不是有效 Base64。') from exc
    returned_payload_bytes = sum(len(payload) for payload in payloads.values())
    if returned_payload_bytes > PAYLOAD_CACHE_BYTES:
        raise EditError('Shader 分片返回载荷超过预算。')
    for index, allocated in registers.items():
        if (not isinstance(allocated, list) or any(type(value) is not int or not 0 <= value <= 255
                                                   for value in allocated)):
            raise EditError('Shader 分片纹理寄存器无效。')
    reflected = {}
    for index, row in reflections.items():
        if not isinstance(row, list) or len(row) != 2 or not isinstance(row[1], dict):
            raise EditError('Shader 分片反射结构无效。')
        try:
            digest = bytes.fromhex(row[0])
        except (ValueError, TypeError) as exc:
            raise EditError('Shader 分片反射摘要无效。') from exc
        source_digest = hashlib.sha256(payloads[index]).digest() if index in payloads else keys_by_index[index][0]
        if len(digest) != 32 or digest != source_digest:
            raise EditError('Shader 分片反射摘要与候选载荷不一致。')
        reflected[index] = (digest, row[1])

    reports = pack['reports']
    if not isinstance(reports, list):
        raise EditError('Shader 分片报告类型错误。')
    report_rows = {}
    for row in reports:
        if not isinstance(row, dict) or set(row) != {'index', 'relocated_reads', 'instance_strides', 'material_markers'}:
            raise EditError('Shader 分片报告字段无效。')
        index = row['index']
        if type(index) is not int or index not in replacements or index in report_rows:
            raise EditError('Shader 分片报告索引越界或重复。')
        if any(type(row[key]) is not int or row[key] < 0
               for key in ('relocated_reads', 'instance_strides', 'material_markers')):
            raise EditError('Shader 分片报告计数无效。')
        report_rows[index] = row
    if set(report_rows) != set(replacements):
        raise EditError('Shader 分片报告与替换载荷不一致。')
    fixed = pack['fixed_shaders']
    if (not isinstance(fixed, list) or any(type(index) is not int for index in fixed)
            or len(fixed) != len(set(fixed)) or not set(fixed) <= assignment
            or any(not keys_by_index[index][3] for index in fixed)):
        raise EditError('Shader 分片固定纹理范围无效。')
    if not set(native_proofs) <= assignment:
        raise EditError('Shader 分片原生纹理范围无效。')
    if any(not keys_by_index[index][3] for index in native_proofs) or set(native_proofs) & set(fixed):
        raise EditError('Shader 分片固定/原生纹理策略冲突。')
    for index, proofs in native_proofs.items():
        if (not isinstance(proofs, list) or not proofs
                or any(not isinstance(row, dict) or set(row) != {'handle', 'index', 'texture'}
                       or any(not isinstance(row[key], str) for key in row) for row in proofs)):
            raise EditError('Shader 分片原生纹理证明无效。')

    counters = pack['counters']
    if not isinstance(counters, dict) or set(counters) != COUNTER_KEYS:
        raise EditError('Shader 分片计数器字段无效。')
    if any(type(value) is not int or value < 0 for value in counters.values()):
        raise EditError('Shader 分片计数器值无效。')
    # 缓存只在产生候选载荷后写入；无改动 Shader 不会占条目。按完整键分组后，
    # 组内第一个改变项是分析/编译项，后续同键项才可能命中缓存。
    first_change = {}
    for index in sorted(assignment):
        if index in replacements:
            first_change.setdefault(keys_by_index[index], index)
    analyzed = set()
    hits = set()
    compiled_keys = set()
    for index in sorted(assignment):
        key = keys_by_index[index]
        if key in compiled_keys:
            hits.add(index)
        else:
            analyzed.add(index)
            if first_change.get(key) == index:
                compiled_keys.add(key)
    expected_analyzed = len(analyzed)
    expected_hits = len(hits)
    compiled = len(compiled_keys)
    expected_counters = {'disassemble': expected_analyzed+2*compiled, 'assemble': 2*compiled,
                         'validate': compiled, 'shader_cache_hits': expected_hits,
                         'analyzed_shaders': expected_analyzed, 'migration_calls': expected_analyzed}
    if counters != expected_counters:
        raise EditError('Shader 分片计数器未完整证明分配范围。')
    durations = pack['durations']
    if not isinstance(durations, dict) or set(durations) != DURATION_KEYS:
        raise EditError('Shader 分片耗时字段无效。')
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
           for value in durations.values()):
        raise EditError('Shader 分片耗时值无效。')
    cache_bytes = pack['cache_bytes']
    if type(cache_bytes) is not int or not 0 <= cache_bytes <= PAYLOAD_CACHE_BYTES:
        raise EditError('Shader 分片载荷缓存超出预算。')
    return {'covered': list(covered), 'replacements': payloads,
            'reports': [report_rows[i] for i in sorted(report_rows)],
            'registers': {i: tuple(registers[i]) for i in sorted(registers)},
            'reflections': {i: reflected[i] for i in sorted(reflected)},
            'native_proofs': native_proofs, 'fixed_shaders': set(fixed),
            'counters': counters, 'durations': durations, 'cache_bytes': cache_bytes}


def _progress_value(path, total):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        if (isinstance(value, dict) and value.get('stage') == 'shaders'
                and type(value.get('completed')) is int
                and value.get('total') == total and 0 <= value['completed'] <= total):
            return value['completed']
    except (OSError, ValueError, TypeError):
        pass
    return 0


# 重复字段视为未知协议；不能让后写值静默覆盖前值。
def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field')
        result[key] = value
    return result


def _communicate(process, data):
    stdout, stderr = process.communicate(data)
    return process.returncode, stdout, stderr


# 由外层隔离 worker 调用；子进程属于同一进程组/Windows Job，失败时主动回收整组。
def run_shards(document, material, dll, actions, record_index, plan_context, plan_digest,
               source_sha256, dxc_sha256, info, texture_shaders, progress, *, workers=MAX_WORKERS):
    assignments = plan_shards(document, info, plan_digest, texture_shaders, workers=workers)
    if len(assignments) <= 1:
        return None
    keys_by_index = {}
    for shader in info['shaders']:
        index = shader['index']
        keys_by_index[index] = (hashlib.sha256(document.shader_bytes(index)).digest(), shader['stage'],
                                plan_digest, index in texture_shaders)
    state = document_state(document)
    command = material_worker_command()
    cwd = None if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    total = len(info['shaders'])
    started = time.monotonic()
    processes = []
    validated = {}
    executor = job = directory = None
    progress('shards', 0, len(assignments))
    last_shader_progress = None
    try:
        directory = tempfile.mkdtemp(prefix='mmtr-material-shards-')
        progress_files = []
        for number, assignment in enumerate(assignments):
            progress_file = Path(directory)/f'progress-{number}.json'
            request = {'protocol': 1, 'op': 'extend_shard', 'dll': str(Path(dll).resolve()),
                       'document': state, 'material': material, 'actions': actions,
                       'record_index': record_index, 'plan_context': plan_context,
                       'expected_plan': plan_digest, 'shard_indices': assignment,
                       'progress_file': str(progress_file)}
            data = json.dumps(request, ensure_ascii=True).encode('utf-8')
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=cwd, start_new_session=os.name != 'nt',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            processes.append(process)
            if os.name == 'nt':
                from .material_control import _WindowsJob
                if job is None:
                    job = _WindowsJob(process)
                else:
                    job.assign(process)
            progress_files.append((progress_file, len(assignment), data))
        executor = ThreadPoolExecutor(max_workers=len(processes))
        pending = {executor.submit(_communicate, processes[number], progress_files[number][2]): number
                   for number in range(len(processes))}
        while pending:
            remaining = SHARD_TIMEOUT_SECONDS-(time.monotonic()-started)
            if remaining <= 0:
                raise EditError('Shader 分片超时，整批候选已丢弃。')
            done, _ = wait(set(pending), timeout=min(0.1, remaining), return_when=FIRST_COMPLETED)
            for future in done:
                number = pending.pop(future)
                process = processes[number]
                try:
                    returncode, stdout, stderr = future.result()
                except Exception as exc:
                    raise EditError(f'Shader 分片 {number} 管道失败：{exc}') from exc
                if returncode:
                    code = returncode & 0xffffffff
                    detail = stderr[-2000:].decode('utf-8', 'replace')
                    raise EditError(f'Shader 分片 {number} 异常退出（0x{code:08X}）：{detail}')
                try:
                    response = json.loads(stdout, object_pairs_hook=_unique_object)
                    if (not isinstance(response, dict) or response.get('protocol') != 1
                            or type(response.get('ok')) is not bool):
                        raise ValueError('invalid envelope')
                    if not response['ok']:
                        raise EditError(response.get('error') or 'Shader 分片失败。')
                    pack = response['result']
                except EditError:
                    raise
                except (ValueError, KeyError, TypeError) as exc:
                    raise EditError(f'Shader 分片 {number} 返回无效结果。') from exc
                validated[number] = validate_pack(pack, set(assignments[number]), keys_by_index,
                    plan_digest=plan_digest, source_sha256=source_sha256, dxc_sha256=dxc_sha256)
                progress('shards', len(validated), len(assignments))
            completed = sum(_progress_value(path, count) for path, count, _ in progress_files)
            completed = min(completed, total)
            if completed != last_shader_progress:
                progress('shaders', completed, total)
                last_shader_progress = completed
        if len(validated) != len(assignments):
            raise EditError('Shader 分片结果不完整。')
        if sum(pack['cache_bytes'] for pack in validated.values()) > MAX_WORKERS*PAYLOAD_CACHE_BYTES:
            raise EditError('所有 Shader 分片载荷缓存超过总预算。')
        covered = [index for number in range(len(assignments)) for index in validated[number]['covered']]
        expected = sorted(shader['index'] for shader in info['shaders'])
        if len(covered) != len(set(covered)) or sorted(covered) != expected:
            raise EditError('所有 Shader 分片合并后覆盖不完整。')
        merged = {'reports': [], 'registers': {}, 'reflections': {}, 'native_proofs': {},
                  'fixed_shaders': set(), 'replacements': {},
                  'counters': {key: 0 for key in COUNTER_KEYS},
                  'durations': {key: 0.0 for key in DURATION_KEYS}}
        for number in range(len(validated)):
            pack = validated[number]
            for key in ('registers', 'reflections', 'native_proofs', 'replacements'):
                if set(pack[key]) & set(merged[key]):
                    raise EditError('Shader 分片合并出现重复索引。')
                merged[key].update(pack[key])
            if merged['fixed_shaders'] & pack['fixed_shaders']:
                raise EditError('Shader 分片合并出现重复固定纹理索引。')
            merged['fixed_shaders'].update(pack['fixed_shaders'])
            merged['reports'].extend(pack['reports'])
            for key, value in pack['counters'].items():
                merged['counters'][key] += value
            for key, value in pack['durations'].items():
                if key != 'planning':
                    merged['durations'][key] += value
        merged['reports'].sort(key=lambda row: row['index'])
        return merged
    finally:
        # 先终止仍未退出的进程组，再等待通信线程；避免卡住的管道读取拖住回收。
        if os.name == 'nt':
            # Job 句柄带 KILL_ON_JOB_CLOSE；关闭一次即可回收其持有的全部分片。
            if job is not None:
                job.close()
            else:
                for process in processes:
                    if process.poll() is None:
                        try:
                            process.kill()
                        except OSError:
                            pass
        else:
            for process in processes:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        for process in processes:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
