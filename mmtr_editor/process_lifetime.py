"""冻结核心跟随 onefile 启动器的生命周期，防止外层终止后遗留内层 worker 所有者。"""
import os
import sys
from threading import Thread


def watch_frozen_parent():
    """仅 Windows 冻结入口调用；内核父进程句柄避免 PID 复用，线程不参与文档事务。"""
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    parent = kernel.OpenProcess(0x00100000, False, os.getppid())  # SYNCHRONIZE
    if not parent:
        raise ctypes.WinError(ctypes.get_last_error())

    def monitor():
        try:
            if kernel.WaitForSingleObject(parent, 0xffffffff) == 0:
                # 启动器已被终止，没有可返回的协议连接。立即退出会关闭全部 Job 句柄，
                # 回收本核心拥有的原生候选；不能等待已失去客户端的线程继续提交。
                os._exit(0)
        finally:
            kernel.CloseHandle(parent)

    Thread(target=monitor, name='mmtr-frozen-parent', daemon=True).start()
