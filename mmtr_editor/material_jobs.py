"""桌面/MCP 共用的会话内批量任务；只在快照和最终提交时持文档锁。

owner 提供 service/lock/revision/dll；Service 自身不包含线程或锁，仍可 deepcopy。
任务状态锁不等待文档锁；提交顺序固定为文档锁→状态锁，查询不触发文档重建。
"""
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import re
from threading import RLock, Thread
import time
import uuid

from .material_control import MaterialCancelled, WorkerControl
from .material_plan import dll_identity
from .material_service import apply_material_batch, prepare_material_batch
from .model import EditError

TERMINAL = frozenset(('succeeded', 'failed', 'cancelled', 'stale'))


class _StaleCandidate(EditError):
    """源状态改变，丢弃候选而不恢复或覆盖用户的新编辑。"""


@dataclass
class _Job:
    """只由管理器状态锁保护的任务记录；不保留完成后的文档二进制。"""
    key: str
    identity: str
    plan: dict
    control: WorkerControl
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = 'queued'
    stage: str = 'queued'
    completed: int = 0
    total: int = 0
    worker_pid: int | None = None
    started: float = field(default_factory=time.monotonic)
    ended: float | None = None
    result: dict | None = None
    error: str | None = None
    details: dict | None = None
    committed_revision: int | None = None
    thread: Thread | None = None


class MaterialJobs:
    """一个会话只执行一项修改；幂等键保留到会话结束，不淘汰后重复执行。"""
    def __init__(self, owner, *, history_limit=128):
        self.owner = owner
        self.lock = RLock()
        self.jobs = {}
        self.keys = {}
        self.history_limit = history_limit
        self.closed = False

    def preview(self, actions, record_index=None):
        with self.owner.lock:
            return prepare_material_batch(self.owner.service, self.owner.dll, actions, record_index,
                                          self.owner.revision)[0].report()

    # 先核对幂等键，再检查当前源；成功后重试不会因新增名称已存在而重新执行。
    def submit(self, actions, record_index, expected_plan, request_key):
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128 or not request_key.strip():
            raise EditError('request_key 必须是 1..128 字符的非空字符串。')
        if not isinstance(expected_plan, str) or not re.fullmatch('[0-9a-f]{64}', expected_plan):
            raise EditError('expected_plan 必须是预览返回的计划 digest。')
        try:
            encoded = json.dumps(dict(actions=actions, record_index=record_index, expected_plan=expected_plan),
                                 sort_keys=True, ensure_ascii=True, allow_nan=False)
            request = json.loads(encoded)  # 调用方之后改动列表不能改变本次请求。
        except (TypeError, ValueError) as exc:
            raise EditError('批次请求必须是有限的 JSON 数据。') from exc
        identity = hashlib.sha256(encoded.encode()).hexdigest()
        with self.owner.lock:
            with self.lock:
                if self.closed:
                    raise EditError('任务会话已关闭。')
                existing = self.keys.get(request_key)
                if existing is not None:
                    job = self.jobs[existing]
                    if job.identity != identity:
                        raise EditError('相同 request_key 不能用于不同的批次或计划。')
                    return self._status(job)
                if any(job.state not in TERMINAL for job in self.jobs.values()):
                    raise EditError('当前会话已有运行中的材质批次，请等待完成或取消。')
                if len(self.jobs) >= self.history_limit:
                    raise EditError('会话任务历史已满；请另开会话，不会淘汰旧请求键后重复执行。')
            candidate = deepcopy(self.owner.service)
            dll = self.owner.dll
            plan = prepare_material_batch(candidate, dll, request['actions'], record_index, self.owner.revision)[0]
            if plan.digest != expected_plan:
                raise EditError('计划身份已改变，必须重新预览。')
            source = (str(candidate.doc.source), str(candidate.mdf.source), candidate.mdf_selected, str(dll))
            control = WorkerControl()
            job = _Job(request_key, identity, plan.report(), control)
            control.progress = lambda **values: self._progress(job, **values)
            job.thread = Thread(target=self._run, args=(job, candidate, dll, request, source),
                                name='mmtr-material-'+job.id[:8], daemon=True)
            with self.lock:
                self.jobs[job.id] = job
                self.keys[request_key] = job.id
                try:
                    job.thread.start()
                except BaseException:
                    del self.jobs[job.id]
                    del self.keys[request_key]
                    raise
                return self._status(job)

    def _progress(self, job, *, stage, completed=0, total=0, worker_pid=None):
        with self.lock:
            if job.state in TERMINAL:
                return
            job.stage, job.completed, job.total = stage, completed, total
            if worker_pid is not None:
                job.worker_pid = worker_pid

    # 候选计算不持 owner.lock；最终身份检查和替换同处一个临界区，取消不能插入半次提交。
    def _run(self, job, candidate, dll, request, source):
        try:
            with self.lock:
                job.control.check()
                job.state, job.stage = 'running', 'preparing'
            result = apply_material_batch(candidate, dll, request['actions'], request['record_index'],
                job.plan['source_revision'], expected_plan=job.plan['digest'], worker_control=job.control)
            job.control.check()
            self._progress(job, stage='committing')
            with self.owner.lock:
                service = self.owner.service
                if (self.owner.revision != job.plan['source_revision'] or service.doc is None or service.mdf is None
                        or (str(service.doc.source), str(service.mdf.source), service.mdf_selected, str(self.owner.dll)) != source
                        or hashlib.sha256(service.doc.build()[0]).hexdigest() != job.plan['source_sha256']
                        or hashlib.sha256(service.mdf.build()[0]).hexdigest() != job.plan['mdf_sha256']
                        or dll_identity(dll) != job.plan['dxc_sha256']):
                    raise _StaleCandidate('源文档、所选材质或 DXC 已改变，候选已丢弃；请重新预览。')
                with self.lock:
                    job.control.check()
                    service.doc, service.mdf = candidate.doc, candidate.mdf
                    service.mdf_link_doc = service.mdf_link_report = service.mdf_migration = None
                    self.owner.revision += 1
                    invalidate = getattr(self.owner, 'invalidate', None)
                    if invalidate is not None:
                        invalidate()
                    job.committed_revision = self.owner.revision
                    job.result = result
                    job.state, job.stage = 'succeeded', 'complete'
                    job.completed = job.total = 1
                    job.ended = time.monotonic()
        except Exception as exc:
            with self.lock:
                job.state = ('cancelled' if isinstance(exc, MaterialCancelled) or job.control.cancelled.is_set() else
                             'stale' if isinstance(exc, _StaleCandidate) else 'failed')
                job.error = str(exc) or type(exc).__name__
                job.details = exc.details() if hasattr(exc, 'details') else None
                job.ended = time.monotonic()

    def _get(self, job_id):
        if job_id not in self.jobs:
            raise EditError('当前会话没有该任务。')
        return self.jobs[job_id]

    def _status(self, job):
        return dict(job_id=job.id, request_key=job.key, plan_digest=job.plan['digest'], state=job.state,
            stage=job.stage, completed=job.completed, total=job.total, worker_pid=job.worker_pid,
            elapsed_seconds=(job.ended or time.monotonic())-job.started, source_revision=job.plan['source_revision'],
            committed_revision=job.committed_revision, error=job.error, details=job.details)

    def status(self, job_id):
        with self.lock:
            return self._status(self._get(job_id))

    def cancel(self, job_id):
        with self.lock:
            job = self._get(job_id)
            if job.state not in TERMINAL:
                job.control.cancelled.set()
                job.state = 'cancelling'
            return self._status(job)

    def result(self, job_id, offset=0, limit=40):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise EditError('结果分页需要 offset>=0，limit 在 1..100 之间。')
        with self.lock:
            job = self._get(job_id)
            if job.state != 'succeeded':
                raise EditError(f'任务尚无可提交结果（{job.state}）：{job.error or job.stage}')
            # 限制最大的 Shader 列表；计划最多100动作，不递归截断默认值/布局字段。
            report = job.result['interface_sync']
            rows = report['shader_reports']
            return {**self._status(job), 'result': {**deepcopy({k: v for k, v in job.result.items() if k != 'interface_sync'}),
                'interface_sync': {**deepcopy({k: v for k, v in report.items() if k != 'shader_reports'}),
                    'shader_reports': deepcopy(rows[offset:offset+limit]), 'shader_reports_total': len(rows),
                    'shader_reports_next_offset': offset+limit if offset+limit < len(rows) else None}}}

    # EOF/正常关闭时等待取消回收；不持文档锁或任务锁 join，避免与候选提交互等。
    def close(self):
        with self.lock:
            self.closed = True
            threads = []
            for job in self.jobs.values():
                if job.state not in TERMINAL:
                    job.control.cancelled.set()
                    job.state = 'cancelling'
                if job.thread is not None:
                    threads.append(job.thread)
        for thread in threads:
            thread.join()
