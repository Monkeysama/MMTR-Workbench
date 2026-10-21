"""材质扩展原生操作的独立进程入口；只读输入字节，不访问游戏路径或发布文件。"""
import hashlib
import json
import os
import sys

from .material_process import document_state, restore_document


# 一个进程完成同一文档的批量DXC工作；原生对象由扩展器/验证器在本进程释放。
def execute(request):
    if request.get('protocol') != 1:
        raise ValueError('Invalid material worker protocol')
    document = restore_document(request['document'])
    if request['op'] == 'validate':
        return document.validate_metadata_interfaces(request['dll'], _in_worker=True)
    if request['op'] == 'usage':
        # 只读统计：同一进程内复用一个反汇编器，结果不含文档状态。
        from .dxil_assembly import DxilAssembly
        from .material_extension import current_layout
        from .material_usage import material_input_usage
        layout = current_layout(document, request['material'])
        compiler = DxilAssembly(request['dll'])
        try:
            return material_input_usage(document, request['material'], layout, compiler.disassemble)
        finally:
            compiler.close()
    if request['op'] != 'extend':
        raise ValueError('Unknown material worker operation')
    from .material_extension import extend_material_interface
    from .material_control import progress_writer
    report = extend_material_interface(document, request['material'], request['dll'],
                                        parameter=request.get('parameter'), texture=request.get('texture'),
                                        record_index=request.get('record_index'), actions=request.get('actions'),
                                        plan_context=request.get('plan_context'), expected_plan=request.get('expected_plan'),
                                        progress=progress_writer(request.get('progress_file')))
    return {'document': document_state(document), 'report': report,
            'sha256': hashlib.sha256(document.build()[0]).hexdigest()}


def main():
    if os.name == 'nt':
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    try:
        result = {'protocol': 1, 'ok': True, 'result': execute(json.loads(sys.stdin.buffer.read()))}
    except Exception as exc:
        result = {'protocol': 1, 'ok': False, 'error': str(exc) or type(exc).__name__}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode('utf-8'))
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
