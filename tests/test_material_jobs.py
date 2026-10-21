"""后台任务的会话/竞争/进程回收验收；合成文档不冒充可执行 Shader。"""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from threading import Event, RLock
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_structure import fixture as mmtr_fixture
from test_mdf_editing import fixture as mdf_fixture
from mmtr_editor.material_control import WorkerControl, controlled_material_process
from mmtr_editor.material_jobs import MaterialJobs, TERMINAL
from mmtr_editor.mdf import MdfDocument
from mmtr_editor.model import Document, EditError
from mmtr_editor.rpc import Service


class MaterialJobTests(unittest.TestCase):
    """计算由可阻塞假 worker 驱动，验证源快照、原子提交、取消和幂等行为。"""
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dll = Path(self.folder.name)/'identity.dll'
        self.dll.write_bytes(b'not loaded; identity only')
        service = Service()
        service.doc = Document(mmtr_fixture(), Path('MaterialShader/test.mmtr.240906211'))
        service.mdf = MdfDocument(mdf_fixture())
        service.mdf_selected = 0
        self.owner = SimpleNamespace(service=service, lock=RLock(), revision=3, dll=str(self.dll))
        self.jobs = MaterialJobs(self.owner)
        self.addCleanup(self.jobs.close)
        self.actions = [{'action': 'parameter', 'name': 'Extra', 'byte_size': 4}]
        self.plan = self.jobs.preview(self.actions, 0)
        self.entered, self.release = Event(), Event()
        self.calls = []
        self.addCleanup(self.release.set)

    def submit(self, key='request'):
        return self.jobs.submit(self.actions, 0, self.plan['digest'], key)

    def fake_apply(self, candidate, dll, actions, record, revision, *, expected_plan, worker_control):
        self.calls.append((actions, expected_plan))
        worker_control.progress(stage='shaders', completed=2, total=10)
        self.entered.set()
        if not self.release.wait(3):
            raise RuntimeError('test gate timed out')
        worker_control.check()
        candidate.mdf.set_property(0, 'Value', [0.75])
        return {'mdf_synced': True, 'target_materials': [0, 1], 'plan': self.plan,
                'interface_sync': {'shader_reports': [{'index': i} for i in range(6)], 'validation': {'valid': True}}}

    def wait(self, job):
        deadline = time.monotonic()+4
        while time.monotonic() < deadline:
            result = self.jobs.status(job['job_id'])
            if result['state'] in TERMINAL:
                return result
            time.sleep(0.01)
        self.fail('job did not reach terminal state')

    def test_success_read_committed_paging_and_retry_after_commit(self):
        before = self.owner.service.mdf.build()[0]
        with patch('mmtr_editor.material_jobs.apply_material_batch', side_effect=self.fake_apply):
            job = self.submit()
            self.assertTrue(self.entered.wait(2))
            self.assertEqual(self.owner.service.mdf.build()[0], before)
            self.assertEqual(self.jobs.status(job['job_id'])['completed'], 2)
            with self.owner.lock, patch.object(self.owner.service.doc, 'build', side_effect=AssertionError('unexpected build')):
                started = time.monotonic()
                self.assertEqual(self.jobs.status(job['job_id'])['state'], 'running')
                self.assertLess(time.monotonic()-started, 0.1)
            self.release.set()
            self.assertEqual(self.wait(job)['state'], 'succeeded')
            self.assertEqual(self.owner.revision, 4)
            self.assertNotEqual(self.owner.service.mdf.build()[0], before)
            self.assertEqual(self.submit()['job_id'], job['job_id'])
            self.assertEqual(len(self.calls), 1)
            result = self.jobs.result(job['job_id'], 2, 2)['result']['interface_sync']
            self.assertEqual([r['index'] for r in result['shader_reports']], [2, 3])
            self.assertEqual((result['shader_reports_total'], result['shader_reports_next_offset']), (6, 4))
            self.assertEqual(self.jobs.cancel(job['job_id'])['state'], 'succeeded')

    def test_dedupe_conflict_active_limit_and_request_copy(self):
        with patch('mmtr_editor.material_jobs.apply_material_batch', side_effect=self.fake_apply):
            job = self.submit()
            self.assertTrue(self.entered.wait(2))
            self.assertEqual(self.submit()['job_id'], job['job_id'])
            with self.assertRaisesRegex(EditError, '相同 request_key'):
                self.jobs.submit([{'action': 'texture', 'name': 'Other'}], 0, self.plan['digest'], 'request')
            with self.assertRaisesRegex(EditError, '已有运行中'):
                self.submit('another')
            self.actions[0]['name'] = 'ChangedAfterSubmit'
            self.release.set()
            self.assertEqual(self.wait(job)['state'], 'succeeded')
            self.assertEqual(self.calls[0][0][0]['name'], 'Extra')
            self.jobs.history_limit = 1
            with self.assertRaisesRegex(EditError, '历史已满'):
                self.submit('full')

    def test_removal_analysis_is_readonly_paged_cancellable_and_deduplicated(self):
        actions = [{'action': 'texture', 'name': 'BaseMap'}]
        before = self.owner.service.doc.build()[0], self.owner.service.mdf.build()[0]

        def analyze(service, dll, actions, material_index, revision, *, worker_control):
            from mmtr_editor.material_removal_service import prepare_removal_preview
            plan, _ = prepare_removal_preview(service, dll, actions, material_index, revision)
            worker_control.progress(stage='analyzing_removal', completed=2, total=10)
            self.entered.set()
            self.release.wait(3)
            worker_control.check()
            return {'plan': plan, 'shaders': [dict(shader_index=i) for i in range(6)], 'failures': [],
                    'eligible': True, 'coverage': {'deletion_supported': False}}

        with patch('mmtr_editor.material_jobs.analyze_removal_preview', side_effect=analyze) as compute:
            job = self.jobs.removal_preview(actions, 0, 'removal')
            self.assertTrue(self.entered.wait(2))
            self.assertEqual(self.jobs.status(job['job_id'])['scope'], 'readonly_analysis')
            self.assertEqual(self.jobs.status(job['job_id'])['completed'], 2)
            self.assertEqual(self.jobs.removal_preview(actions, 0, 'removal')['job_id'], job['job_id'])
            with self.assertRaisesRegex(EditError, '相同 request_key'):
                self.jobs.removal_preview(actions, 1, 'removal')
            with self.assertRaisesRegex(EditError, '已有运行中'):
                self.submit('another')
            self.release.set()
            self.assertEqual(self.wait(job)['state'], 'succeeded')
            self.assertEqual(compute.call_count, 1)
            page = self.jobs.result(job['job_id'], 1, 2)['result']
            self.assertEqual([s['shader_index'] for s in page['shaders']], [1, 2])
            self.assertEqual((page['shaders_total'], page['shaders_next_offset']), (6, 3))
            self.assertEqual(self.owner.revision, 3)
            self.assertEqual(before, (self.owner.service.doc.build()[0], self.owner.service.mdf.build()[0]))
            self.assertFalse(self.jobs.status(job['job_id'])['candidate_available'])

    def test_removal_dxc_source_change_and_cancel_do_not_publish_analysis(self):
        actions = [{'action': 'texture', 'name': 'BaseMap'}]
        for mode in ('source', 'dxc', 'cancel'):
            self.entered.clear()
            self.release.clear()

            def analyze(service, dll, actions, material_index, revision, *, worker_control):
                from mmtr_editor.material_removal_service import prepare_removal_preview
                plan, _ = prepare_removal_preview(service, dll, actions, material_index, revision)
                self.entered.set()
                self.release.wait(3)
                worker_control.check()
                return {'plan': plan, 'shaders': [], 'failures': []}

            with self.subTest(mode=mode), patch('mmtr_editor.material_jobs.analyze_removal_preview', side_effect=analyze):
                job = self.jobs.removal_preview(actions, 0, mode)
                self.assertTrue(self.entered.wait(2))
                if mode == 'source':
                    self.owner.service.mdf.set_property(0, 'Value', [0.375])
                elif mode == 'dxc':
                    self.dll.write_bytes(b'changed during analysis')
                else:
                    self.jobs.cancel(job['job_id'])
                self.release.set()
                self.assertEqual(self.wait(job)['state'], 'cancelled' if mode == 'cancel' else 'stale')
                with self.assertRaises(EditError):
                    self.jobs.result(job['job_id'])
                self.assertEqual(self.owner.revision, 3)

    def test_cancel_preserves_source_and_close_reaps_thread(self):
        before = self.owner.service.doc.build()[0], self.owner.service.mdf.build()[0]
        with patch('mmtr_editor.material_jobs.apply_material_batch', side_effect=self.fake_apply):
            job = self.submit()
            self.assertTrue(self.entered.wait(2))
            self.assertEqual(self.jobs.cancel(job['job_id'])['state'], 'cancelling')
            self.release.set()
            self.jobs.close()
            self.assertEqual(self.wait(job)['state'], 'cancelled')
            self.assertFalse(self.jobs.jobs[job['job_id']].thread.is_alive())
            self.assertEqual(self.owner.revision, 3)
            self.assertEqual(before, (self.owner.service.doc.build()[0], self.owner.service.mdf.build()[0]))
            with self.assertRaisesRegex(EditError, '已关闭'):
                self.submit('closed')

    def test_stale_source_revision_selection_and_dxc_never_overwrite_newer_state(self):
        mutations = [lambda: setattr(self.owner, 'revision', 4),
                     lambda: self.owner.service.mdf.set_property(0, 'Value', [0.25]),
                     lambda: setattr(self.owner.service, 'mdf_selected', 1),
                     lambda: self.dll.write_bytes(b'changed DLL')]
        for number, mutate in enumerate(mutations):
            with self.subTest(number=number):
                self.plan = self.jobs.preview(self.actions, 0)
                self.entered.clear()
                self.release.clear()
                with patch('mmtr_editor.material_jobs.apply_material_batch', side_effect=self.fake_apply):
                    job = self.submit(str(number))
                    self.assertTrue(self.entered.wait(2))
                    with self.owner.lock:
                        mutate()
                        current = self.owner.service.doc.build()[0], self.owner.service.mdf.build()[0]
                    self.release.set()
                    self.assertEqual(self.wait(job)['state'], 'stale')
                    self.assertEqual(current, (self.owner.service.doc.build()[0], self.owner.service.mdf.build()[0]))

    def test_failure_bad_plan_and_arguments_preserve_documents(self):
        for key, digest in [('', self.plan['digest']), ('key', 'invalid')]:
            with self.assertRaises(EditError):
                self.jobs.submit(self.actions, 0, digest, key)
        with self.assertRaisesRegex(EditError, '重新预览'):
            self.jobs.submit(self.actions, 0, '0'*64, 'stale')
        with patch('mmtr_editor.material_jobs.apply_material_batch', side_effect=EditError('native failure')):
            job = self.submit()
            self.assertEqual(self.wait(job)['state'], 'failed')
            self.assertEqual(self.owner.revision, 3)
            self.assertEqual(self.submit()['job_id'], job['job_id'])
            with self.assertRaises(EditError):
                self.jobs.result(job['job_id'])
        with self.assertRaises(EditError):
            self.jobs.status('missing')

    def test_cancel_while_candidate_waits_to_commit_preserves_document(self):
        before = self.owner.service.mdf.build()[0]
        with patch('mmtr_editor.material_jobs.apply_material_batch', side_effect=self.fake_apply):
            job = self.submit()
            self.assertTrue(self.entered.wait(2))
            with self.owner.lock:
                self.release.set()
                deadline = time.monotonic()+2
                while self.jobs.status(job['job_id'])['stage'] != 'committing' and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(self.jobs.status(job['job_id'])['stage'], 'committing')
                self.assertEqual(self.jobs.cancel(job['job_id'])['state'], 'cancelling')
            self.assertEqual(self.wait(job)['state'], 'cancelled')
            self.assertEqual(self.owner.service.mdf.build()[0], before)
            self.assertEqual(self.owner.revision, 3)

    def test_close_during_snapshot_never_starts_late_worker(self):
        from mmtr_editor.material_jobs import prepare_material_batch

        def closing(*args, **kwargs):
            result = prepare_material_batch(*args, **kwargs)
            self.jobs.close()
            return result

        with patch('mmtr_editor.material_jobs.prepare_material_batch', side_effect=closing), \
                patch('mmtr_editor.material_jobs.apply_material_batch') as worker:
            with self.assertRaisesRegex(EditError, '已关闭'):
                self.submit()
            worker.assert_not_called()
        self.assertEqual(self.jobs.jobs, {})


class ControlledMaterialProcessTests(unittest.TestCase):
    """真实 Python 子进程验证管道、大输入、进度、超时与取消，不依赖 DXC。"""
    def run_child(self, code, control=None, timeout=3, **request):
        return controlled_material_process([sys.executable, '-c', code], {'op': 'test', **request},
                                           cwd=None, timeout=timeout, control=control or WorkerControl())

    def test_large_input_and_progress_keep_protocol_clean(self):
        events = []
        result = self.run_child('import sys,json,time,pathlib; r=json.load(sys.stdin); '
            'pathlib.Path(r["progress_file"]).write_text(json.dumps(dict(stage="shaders",completed=2,total=8))); '
            'time.sleep(.3); print(json.dumps(dict(length=len(r["payload"]))))',
            WorkerControl(lambda **value: events.append(value)), payload='x'*2000000)
        self.assertEqual(json.loads(result.stdout)['length'], 2000000)
        self.assertEqual(result.stderr, b'')
        self.assertIn('shaders', [event['stage'] for event in events])

    def test_cancel_timeout_crash_and_callback_failure_reap_owned_child(self):
        for mode in ('cancel', 'timeout', 'callback'):
            events = []
            control = WorkerControl()

            def event(**values):
                events.append(values)
                if mode == 'cancel':
                    control.cancelled.set()
                if mode == 'callback':
                    raise RuntimeError('callback failure')

            control.progress = event
            with self.subTest(mode=mode), self.assertRaises(Exception):
                self.run_child('import sys,time; sys.stdin.read(); time.sleep(30)', control, timeout=.3)
            self.assertTrue(events)
            self.assertFalse(self.alive(events[0]['worker_pid']))
        result = self.run_child('import sys; sys.stdin.read(); sys.exit(7)')
        self.assertEqual(result.returncode, 7)

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Object 父进程退出回收验收')
    def test_parent_crash_closes_job_and_reaps_worker(self):
        # 真实父进程被强制终止；不依赖 finally/EOF 才回收原生 worker。
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory)/'pid.json'
            code = (
                'from pathlib import Path; import json,sys; '
                'from mmtr_editor.material_control import controlled_material_process,WorkerControl; '
                'control=WorkerControl(lambda **v: Path(sys.argv[1]).write_text(json.dumps(v))); '
                'controlled_material_process([sys.executable,"-c","import sys,time; sys.stdin.read(); time.sleep(30)"], '
                '{"op":"test"},cwd=None,timeout=30,control=control)'
            )
            parent = subprocess.Popen([sys.executable, '-c', code, str(marker)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic()+4
                while not marker.exists() and parent.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(marker.exists(), 'parent failed before worker ownership')
                pid = json.loads(marker.read_text())['worker_pid']
                self.assertTrue(self.alive(pid))
                parent.kill()
                parent.communicate(timeout=3)
                deadline = time.monotonic()+3
                while self.alive(pid) and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertFalse(self.alive(pid))
            finally:
                if parent.poll() is None:
                    parent.kill()
                parent.communicate(timeout=3)

    @staticmethod
    def alive(pid):
        if os.name == 'nt':
            import ctypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            code = ctypes.c_ulong()
            kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
            kernel.CloseHandle(handle)
            return code.value == 259
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
