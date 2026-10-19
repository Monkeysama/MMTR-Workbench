"""隔离材质接口原生DXC操作；父进程持有文档，子进程只返回完整候选和验证结果。"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from .dxc_process import DxcProcessError
from .model import Document, EditError, validate_shader


# 工作状态显式序列化，不传递文件写入能力；保留用户所有未导出的原始编辑。
def document_state(document):
    return {'source': str(document.source), 'original': base64.b64encode(document.original).decode('ascii'),
            'replacements': {str(i): base64.b64encode(data).decode('ascii') for i, data in document.replacements.items()},
            'replacement_notes': document.replacement_notes, 'renames': document.renames,
            'redirects': [[record, field, index] for (record, field), index in document.redirects.items()],
            'metadata_edits': document.metadata_edits}


def restore_document(state):
    document = Document(base64.b64decode(state['original'], validate=True), Path(state['source']))
    document.replacements = {int(i): base64.b64decode(data, validate=True) for i, data in state['replacements'].items()}
    for data in document.replacements.values():
        validate_shader(data)
    document.replacement_notes = {int(i): note for i, note in state['replacement_notes'].items()}
    document.renames = {int(i): name for i, name in state['renames'].items()}
    document.redirects = {(record, field): index for record, field, index in state['redirects']}
    document.metadata_edits = state['metadata_edits']
    return document


# 单次批处理独占一个可回收进程，避免逐Shader启动进程；原生崩溃/超时不影响编辑器。
def run_material_task(document, operation, dll, *, timeout=300, **arguments):
    command = ([sys.executable, '--material-worker'] if getattr(sys, 'frozen', False)
               else [sys.executable, '-m', 'mmtr_editor.material_worker'])
    request = {'protocol': 1, 'op': operation, 'dll': str(Path(dll).resolve()),
               'document': document_state(document), **arguments}
    try:
        completed = subprocess.run(command, input=json.dumps(request, ensure_ascii=True).encode('utf-8'),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   cwd=None if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1],
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                   timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise DxcProcessError('材质接口同步超时，本次操作已取消，当前文档和修改已保留。',
                              kind='timeout', operation=operation) from exc
    except OSError as exc:
        raise DxcProcessError(f'无法启动材质接口同步：{exc}', kind='launch', operation=operation) from exc
    if completed.returncode:
        code = completed.returncode & 0xffffffff
        raise DxcProcessError(f'材质接口DXC进程异常退出（0x{code:08X}），当前文档和修改已保留。',
                              kind='crash', operation=operation, exit_code=code)
    try:
        response = json.loads(completed.stdout)
        if not isinstance(response, dict) or response.get('protocol') != 1 or type(response.get('ok')) is not bool:
            raise ValueError('invalid envelope')
        if not response['ok']:
            raise EditError(response.get('error') or '材质接口同步失败。')
        result = response['result']
        if not isinstance(result, dict):
            raise ValueError('invalid result')
        return result
    except (ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, EditError):
            raise
        raise DxcProcessError('材质接口进程返回无效结果，未应用任何修改。',
                              kind='protocol', operation=operation) from exc


# 父进程重新核对源身份、容器、完整性和hash后才提交；失败不替换原编辑状态。
def extend_in_worker(document, material, dll, **arguments):
    from .mdf import _public_data
    result = run_material_task(document, 'extend', dll, material=_public_data(material), **arguments)
    candidate = restore_document(result['document'])
    if (candidate.source != document.source or candidate.original != document.original
            or candidate.renames != document.renames or candidate.redirects != document.redirects):
        raise EditError('接口同步意外改变文档源身份或已有改名/变体引用。')
    rebuilt, _ = candidate.build()
    if hashlib.sha256(rebuilt).hexdigest() != result['sha256'] or not result['report']['validation']['valid']:
        raise EditError('接口同步结果hash或验证报告不一致。')
    reopened = Document(rebuilt, document.source)
    if reopened.integrity['name_hash_mismatches'] or reopened.integrity['shader_trailer_mismatches']:
        raise EditError('接口同步结果完整性检查失败。')
    document.replacements, document.replacement_notes = candidate.replacements, candidate.replacement_notes
    document.metadata_edits = candidate.metadata_edits
    return result['report']
