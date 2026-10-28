"""受控 Shader 分片的纯 CPU 协议测试；不依赖游戏资产或原生 DXC。"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from test_structure import fixture
from mmtr_editor.material_parallel import DURATION_KEYS, plan_shards, validate_pack, run_shards
from mmtr_editor.material_process import document_state
from mmtr_editor.material_worker import execute
from mmtr_editor.model import Document, EditError


# 合成 MMTR 的四个记录共用同一 Shader；计划必须按缓存键聚合而不是按记录轮转。
class MaterialShardTests(unittest.TestCase):
    def setUp(self):
        self.document = Document(fixture(), Path('MaterialShader/test.mmtr.240906211'))
        # fixture 只有一个 Shader 程序；分片协议按信息表中的程序索引验证。
        self.info = self.document.current_info()
        # 合成 fixture 的四个记录实际共用同一 Shader；改索引还会影响纹理固定/动态策略。
        self.digest = '1'*64
        self.key = (hashlib.sha256(self.document.shader_bytes(0)).digest(), 'ps', self.digest, False)

    def plan(self, workers=4):
        return plan_shards(self.document, self.info, self.digest, set(), workers=workers)

    def test_same_cache_key_stays_in_one_shard_and_all_indices_are_covered_once(self):
        shards = self.plan()
        self.assertEqual(shards, [[0]])

    def test_worker_and_total_source_budget_are_bounded(self):
        with self.assertRaisesRegex(EditError, 'worker'):
            self.plan(workers=5)
        with mock.patch('mmtr_editor.material_parallel.SHARD_SOURCE_BYTES', 60):
            self.assertEqual(self.plan(), [[0]])

    def test_exact_pack_is_accepted_and_identity_or_coverage_tampering_is_rejected(self):
        index = 0
        payload = self.document.shader_bytes(index)
        keys = {index: self.key}
        pack = {'plan_digest': self.digest, 'source_sha256': '2'*64, 'dxc_sha256': '3'*64,
                'covered': [index], 'replacements': {str(index): base64.b64encode(payload).decode()},
                'reports': [{'index': index, 'relocated_reads': 0, 'instance_strides': 0, 'material_markers': 0}],
                'registers': {str(index): []}, 'reflections': {str(index): [hashlib.sha256(payload).hexdigest(), {}]},
                'native_proofs': {}, 'fixed_shaders': [],
                'counters': {'disassemble': 3, 'assemble': 2, 'validate': 1, 'shader_cache_hits': 0,
                             'analyzed_shaders': 1, 'migration_calls': 1},
                'durations': {key: 0.0 for key in DURATION_KEYS}, 'cache_bytes': len(payload)}
        result = validate_pack(pack, {index}, keys, plan_digest=self.digest,
                               source_sha256='2'*64, dxc_sha256='3'*64)
        self.assertEqual(result['replacements'][index], payload)
        for field, value in (('covered', [1]), ('plan_digest', '4'*64), ('cache_bytes', 64*1024*1024+1)):
            bad = {**pack, field: value}
            with self.subTest(field=field), self.assertRaises(EditError):
                validate_pack(bad, {index}, keys, plan_digest=self.digest,
                              source_sha256='2'*64, dxc_sha256='3'*64)
        bad = {**pack, 'replacements': {str(index): base64.b64encode(b'x').decode()}}
        with self.assertRaisesRegex(EditError, '摘要'):
            validate_pack(bad, {index}, keys, plan_digest=self.digest,
                          source_sha256='2'*64, dxc_sha256='3'*64)
        with mock.patch('mmtr_editor.material_parallel.PAYLOAD_CACHE_BYTES', len(payload)-1):
            with self.assertRaisesRegex(EditError, '返回载荷'):
                validate_pack(pack, {index}, keys, plan_digest=self.digest,
                              source_sha256='2'*64, dxc_sha256='3'*64)


    def test_any_failed_shard_rejects_whole_batch_without_mutation(self):
        # 协议失败路径：第一片返回合法包，第二片失败；不得保留任何部分结果。
        payload = self.document.shader_bytes(0)
        good = {'plan_digest': self.digest, 'source_sha256': '2'*64, 'dxc_sha256': '3'*64,
                'covered': [0], 'replacements': {'0': base64.b64encode(payload).decode()},
                'reports': [{'index': 0, 'relocated_reads': 0, 'instance_strides': 0, 'material_markers': 0}],
                'registers': {'0': []},
                'reflections': {'0': [hashlib.sha256(payload).hexdigest(), {}]},
                'native_proofs': {}, 'fixed_shaders': [],
                'counters': {'disassemble': 3, 'assemble': 2, 'validate': 1,
                             'shader_cache_hits': 0, 'analyzed_shaders': 1, 'migration_calls': 1},
                'durations': {key: 0.0 for key in DURATION_KEYS}, 'cache_bytes': len(payload)}

        class Process:
            def __init__(self, number):
                self.number = number
                self.pid = 1000+number
                self.returncode = None
                self._handle = number+1
                self.stdin = self.stdout = self.stderr = None
            def communicate(self, data):
                self.returncode = 0
                if self.number == 1:
                    return b'{"protocol":1,"ok":false,"error":"injected shard failure"}', b''
                return json.dumps({'protocol': 1, 'ok': True, 'result': good}).encode(), b''
            def poll(self):
                return self.returncode
            def wait(self):
                return self.returncode

        before = self.document.build()[0]
        processes = []
        def launch(*args, **kwargs):
            process = Process(len(processes))
            processes.append(process)
            return process
        with mock.patch('mmtr_editor.material_parallel.plan_shards', return_value=[[0], [1]]), \
             mock.patch('mmtr_editor.material_parallel.document_state', return_value={'source': 'x'}), \
             mock.patch('mmtr_editor.material_parallel.material_worker_command', return_value=['worker']), \
             mock.patch('mmtr_editor.material_parallel.subprocess.Popen', side_effect=launch), \
             mock.patch('mmtr_editor.material_control._WindowsJob') as job:
            with self.assertRaisesRegex(EditError, 'injected shard failure'):
                run_shards(self.document, {}, 'C:/fake.dll', [], None, {}, self.digest,
                           '2'*64, '3'*64, self.info, set(), lambda *args: None, workers=2)
        self.assertEqual(before, self.document.build()[0])
        self.assertTrue(all(process.returncode == 0 for process in processes))
        # Windows 回收 Job，POSIX 分支不会创建 Windows Job。
        if os.name == 'nt':
            job.return_value.close.assert_called_once()
        else:
            job.assert_not_called()

    def test_shard_protocol_requires_explicit_indices(self):
        request = {'protocol': 1, 'op': 'extend_shard', 'document': document_state(self.document)}
        with self.assertRaisesRegex(ValueError, '分片索引缺失'):
            execute(request)

    @unittest.skipUnless(os.name == 'nt', 'Windows Job 分配失败的真实进程回收验收')
    def test_job_assignment_failure_reaps_all_started_children_without_mutation(self):
        from mmtr_editor.material_control import _WindowsJob

        before = self.document.build()[0]
        real_popen = subprocess.Popen
        # 第一片尚无 Job，第二片已有 Job；两种失败都必须回收所有已启动进程。
        for fail_at in (1, 2):
            with self.subTest(fail_at=fail_at):
                launched, assigned = [], []

                class FailingJob(_WindowsJob):
                    def assign(self, process):
                        assigned.append(process.pid)
                        if len(assigned) == fail_at:
                            raise OSError('injected Job assignment failure')
                        super().assign(process)

                def launch(*args, **kwargs):
                    process = real_popen(*args, **kwargs)
                    launched.append(process)
                    return process

                try:
                    with mock.patch('mmtr_editor.material_parallel.plan_shards', return_value=[[0], [0]]), \
                         mock.patch('mmtr_editor.material_parallel.material_worker_command',
                                    return_value=[sys.executable, '-c', 'import time; time.sleep(60)']), \
                         mock.patch('mmtr_editor.material_parallel.subprocess.Popen', side_effect=launch), \
                         mock.patch('mmtr_editor.material_control._WindowsJob', FailingJob):
                        with self.assertRaisesRegex(OSError, 'injected Job assignment failure'):
                            run_shards(self.document, {}, 'C:/fake.dll', [], None, {}, self.digest,
                                       '2'*64, '3'*64, self.info, set(), lambda *args: None, workers=2)
                    self.assertEqual(len(launched), fail_at)
                    for process in launched:
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            self.fail(f'Job 分配失败后子进程 {process.pid} 仍在运行。')
                        self.assertTrue(all(stream.closed for stream in
                                            (process.stdin, process.stdout, process.stderr)))
                    self.assertEqual(before, self.document.build()[0])
                finally:
                    # 测试失败时也只回收本用例创建的进程，避免污染后续验收。
                    for process in launched:
                        if process.poll() is None:
                            process.kill()
                        process.wait(timeout=3)

    def test_timeout_kills_all_real_shard_processes(self):
        # 只验证整体 deadline 的实际进程回收；重复分配在超时前不会进入覆盖成功路径。
        before = self.document.build()[0]
        launched = []
        real_popen = subprocess.Popen

        def launch(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            launched.append(process)
            return process

        with mock.patch('mmtr_editor.material_parallel.plan_shards', return_value=[[0], [0]]), \
             mock.patch('mmtr_editor.material_parallel.material_worker_command',
                        return_value=[sys.executable, '-c', 'import time; time.sleep(60)']), \
             mock.patch('mmtr_editor.material_parallel.SHARD_TIMEOUT_SECONDS', 0.5), \
             mock.patch('mmtr_editor.material_parallel.subprocess.Popen', side_effect=launch):
            with self.assertRaisesRegex(EditError, '超时'):
                run_shards(self.document, {}, 'C:/fake.dll', [], None, {}, self.digest,
                           '2'*64, '3'*64, self.info, set(), lambda *args: None, workers=2)
        self.assertEqual(before, self.document.build()[0])
        self.assertEqual(len(launched), 2)
        for process in launched:
            process.wait(timeout=2)
            self.assertIsNotNone(process.poll())


if __name__ == '__main__':
    unittest.main()
