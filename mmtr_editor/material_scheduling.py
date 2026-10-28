"""材质任务开始时选择固定并发上限；内存数值是保守调度估计，不是硬 RSS 限制。"""
import os

from .model import EditError

MAX_WORKERS = 8
MIB = 1024 * 1024
MEMORY_RESERVE = 1024 * MIB


def validate_max_workers(value):
    """空值表示自动；手动值只降低上限，不能越过硬件及工作集约束。"""
    if value is not None and (type(value) is not int or not 1 <= value <= MAX_WORKERS):
        raise EditError('max_workers 必须为空或 1..8 的整数。')
    return value


def available_resources():
    """优先读取进程可用 CPU，旧 Python 使用平台亲和性；探测失败保守串行。"""
    cpu = getattr(os, 'process_cpu_count', lambda: None)()
    memory = None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes as w
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        if cpu is None:
            kernel.GetCurrentProcess.restype = w.HANDLE
            kernel.GetProcessAffinityMask.argtypes = [w.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                                      ctypes.POINTER(ctypes.c_size_t)]
            kernel.GetProcessAffinityMask.restype = w.BOOL
            process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
            if kernel.GetProcessAffinityMask(kernel.GetCurrentProcess(), ctypes.byref(process_mask),
                                             ctypes.byref(system_mask)):
                cpu = process_mask.value.bit_count()

        class Memory(ctypes.Structure):
            _fields_ = [('length', w.DWORD), ('load', w.DWORD)] + [
                (name, ctypes.c_uint64) for name in ('total', 'available', 'page_total', 'page_available',
                                                    'virtual_total', 'virtual_available', 'extended')]
        state = Memory()
        state.length = ctypes.sizeof(state)
        kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(Memory)]
        kernel.GlobalMemoryStatusEx.restype = w.BOOL
        if kernel.GlobalMemoryStatusEx(ctypes.byref(state)):
            memory = state.available
    else:
        if cpu is None and hasattr(os, 'sched_getaffinity'):
            try:
                cpu = len(os.sched_getaffinity(0))
            except OSError:
                pass
        try:
            memory = os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        except (OSError, ValueError, AttributeError):
            pass
    return cpu, memory


def select_workers(source_bytes, max_workers=None):
    """CPU 留半数逻辑核，内存留 1GiB，再按文档复制和载荷缓存估算每片成本。"""
    validate_max_workers(max_workers)
    cpu, memory = available_resources()
    cpu_limit = max(1, cpu // 2) if cpu else 1
    per_worker = max(256 * MIB, source_bytes * 4 + 128 * MIB)
    memory_limit = max(1, (memory - MEMORY_RESERVE) // per_worker) if memory is not None else 1
    limit = min(MAX_WORKERS, max_workers or MAX_WORKERS, cpu_limit, memory_limit)
    reasons = ['cpu_half', 'memory_budget']
    if max_workers is not None:
        reasons.append('requested_cap')
    if not cpu or memory is None:
        reasons.append('resources_unavailable')
    return dict(mode='auto' if max_workers is None else 'capped', requested_max_workers=max_workers,
                cpu_available=cpu, cpu_limit=cpu_limit, memory_available_bytes=memory,
                memory_limit=memory_limit, estimated_worker_bytes=per_worker,
                reserved_memory_bytes=MEMORY_RESERVE, policy_limit=limit, reasons=reasons)
