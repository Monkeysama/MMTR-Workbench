"""Run native DXC outside the document owner; never execute shader code."""
import base64
import binascii
import json
import os
from pathlib import Path
import subprocess
import sys

from .model import EditError,validate_shader

DEFAULT_TIMEOUT=120


class DxcProcessError(EditError):
    def __init__(self,message,*,kind,operation,exit_code=None):
        super().__init__(message)
        self.kind,self.operation,self.exit_code=kind,operation,exit_code

    def details(self):
        return dict(kind=self.kind,operation=self.operation,exit_code=self.exit_code)


def worker_command():
    # Frozen sidecars re-enter their own entry point. A frozen executable is
    # not a Python interpreter and cannot be started with `-m`.
    if getattr(sys,'frozen',False):return [sys.executable,'--dxc-worker']
    return [sys.executable,'-m','mmtr_editor.dxc_worker']


# 原生操作只在子进程执行；父进程负责超时回收、协议和产物结构校验。
def run_worker(dll,operation,*,timeout=DEFAULT_TIMEOUT,**arguments):
    if operation not in ('compile','disassemble','assemble'):raise ValueError('Unknown DXC operation')
    label={'compile':'编译','disassemble':'反汇编','assemble':'组装与校验'}[operation]
    request=dict(protocol=1,op=operation,dll=str(Path(dll).resolve()),**arguments)
    try:
        completed=subprocess.run(worker_command(),input=json.dumps(request,ensure_ascii=True).encode('utf-8'),
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 cwd=None if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1],
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
                                 timeout=timeout,check=False)
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills and reaps its child before raising. No worker
        # result can arrive later and replace a newer compile.
        raise DxcProcessError(f'DXC {label}超过 {timeout:g} 秒，已结束本次任务。当前 MMTR 文档和修改已保留。',
                              kind='timeout',operation=operation) from exc
    except OSError as exc:
        raise DxcProcessError(f'无法启动 DXC {label}进程：{exc}。当前 MMTR 文档和修改已保留。',
                              kind='launch',operation=operation) from exc
    if completed.returncode:
        code=completed.returncode&0xffffffff
        raise DxcProcessError(f'DXC {label}进程异常退出（0x{code:08X}）。当前 MMTR 文档和修改已保留。',
                              kind='crash',operation=operation,exit_code=code)
    try:
        response=json.loads(completed.stdout)
        if not isinstance(response,dict) or response.get('protocol')!=1 or type(response.get('ok')) is not bool:
            raise ValueError('Invalid response envelope')
        if not response['ok']:
            error=response.get('error')
            if not isinstance(error,str) or not error:raise ValueError('Missing worker error')
            raise DxcProcessError(error,kind='diagnostic',operation=operation)
        result=response['result']
        if not isinstance(result,dict) or not isinstance(result.get('ir'),str) or not result['ir'].strip():
            raise ValueError('Missing DXIL text')
        if operation in ('compile','assemble'):
            if not isinstance(result.get('warnings'),str):raise ValueError('Missing compiler diagnostics')
            payload=base64.b64decode(result['payload'],validate=True)
            validate_shader(payload)
            result={**result,'payload':payload}
        return result
    except DxcProcessError:raise
    except (ValueError,KeyError,TypeError,binascii.Error) as exc:
        raise DxcProcessError(f'DXC {label}返回了无效结果，未应用任何编译产物。',
                              kind='protocol',operation=operation) from exc


def compile_hlsl(dll,source,entry,profile,*,timeout=DEFAULT_TIMEOUT):
    result=run_worker(dll,'compile',source=source,entry=entry,profile=profile,timeout=timeout)
    return result['payload'],result['warnings'],result['ir']


def disassemble_shader(dll,payload,*,timeout=DEFAULT_TIMEOUT):
    return run_worker(dll,'disassemble',payload=base64.b64encode(payload).decode('ascii'),timeout=timeout)['ir']


# 独立子进程依次组装、校验并反汇编；失败时不返回任何可应用产物。
def assemble_dxil(dll,ir,*,timeout=DEFAULT_TIMEOUT):
    """Assemble and validate in an expendable worker, returning fresh DXIL text."""
    result=run_worker(dll,'assemble',ir=ir,timeout=timeout)
    return result['payload'],result['warnings'],result['ir']
