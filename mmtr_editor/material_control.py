"""材质后台任务的本地控制；事件/回调不进入 worker JSON，stdout 只保留协议。

每个控制器只归一个任务所有。Windows Job Object 关闭时回收子进程树；
子进程先等 stdin，分配 Job 成功后才发送文档，避免原生工作逃离所有权。
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from threading import Event
import time

from .dxc_process import DxcProcessError


class MaterialCancelled(DxcProcessError):
    """候选被取消，调用方不得把候选提交为当前文档。"""
    def __init__(self):
        super().__init__('材质批次已取消，当前文档未改变。', kind='cancelled', operation='extend')


class WorkerControl:
    """任务线程持有的取消事件和进度出口；查询方只读取任务管理器缓存。"""
    def __init__(self, progress=None):
        self.cancelled = Event()
        self.progress = progress or (lambda **_: None)

    def check(self):
        if self.cancelled.is_set():
            raise MaterialCancelled()


class _WindowsJob:
    """只包含本次 worker 的内核 Job；句柄不继承，所有异常路径均关闭。"""
    def __init__(self, process):
        import ctypes
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', w.DWORD), ('min_working', ctypes.c_size_t),
                        ('max_working', ctypes.c_size_t), ('active', w.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', Io), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.kernel.SetInformationJobObject.restype = w.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = w.BOOL
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = Extended()
            info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.assign(process)
        except BaseException:
            self.close()
            raise

    def assign(self, process):
        if not self.handle or not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


# 进程控制只接触本次 Popen；取消/超时先关闭 Job，再 communicate 回收管道和进程。
def controlled_material_process(command, request, *, cwd, timeout, control):
    control.check()
    started = time.monotonic()
    process = job = None
    with tempfile.TemporaryDirectory(prefix='mmtr-material-job-') as directory:
        progress_file = Path(directory)/'progress.json'
        data = json.dumps({**request, 'progress_file': str(progress_file)}, ensure_ascii=True).encode('utf-8')
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=cwd, start_new_session=os.name != 'nt',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if os.name == 'nt':
                job = _WindowsJob(process)
            control.progress(stage='starting', completed=0, total=0, worker_pid=process.pid)
            last_progress = None
            while True:
                control.check()
                if time.monotonic()-started >= timeout:
                    raise DxcProcessError('材质后台任务超时，候选已丢弃。', kind='timeout', operation=request['op'])
                try:
                    # 首次 communicate 发送输入，重试时 Python 保留未写完的输入和已读管道。
                    stdout, stderr = process.communicate(input=data, timeout=min(0.1, max(0.001, timeout-(time.monotonic()-started))))
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    data = None
                    try:
                        raw = progress_file.read_bytes()
                        if raw != last_progress:
                            progress = json.loads(raw)
                            if (isinstance(progress, dict) and isinstance(progress.get('stage'), str)
                                    and type(progress.get('completed')) is int and type(progress.get('total')) is int
                                    and 0 <= progress['completed'] <= progress['total']):
                                control.progress(stage=progress['stage'], completed=progress['completed'], total=progress['total'])
                                last_progress = raw
                    except (OSError, ValueError):
                        pass  # 原子替换前尚无进度文件；不能把进度缺失误报为计算失败。
        finally:
            if job is not None:
                job.close()
            if process is not None:
                if process.poll() is None:
                    if os.name == 'nt':
                        process.kill()
                    else:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                process.communicate()


# 只在 worker 内调用；写自己的私有临时目录，不向协议 stdout 输出进度。
def progress_writer(path):
    if path is None:
        return None
    target = Path(path)
    previous = [None, 0.0]

    def report(stage, completed=0, total=0):
        now = time.monotonic()
        if stage == previous[0] and completed != total and now-previous[1] < 0.1:
            return
        pending = target.with_suffix('.pending')
        pending.write_text(json.dumps(dict(stage=stage, completed=completed, total=total)), encoding='utf-8')
        os.replace(pending, target)
        previous[:] = stage, now
    return report
