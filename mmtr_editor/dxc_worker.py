"""Single native DXC task over stdin/stdout. Has no document or output path."""
import base64
import json
import os
import sys

from .dxc import Dxc


# 单次请求独占原生对象，结束时释放；不持有编辑器文档或输出路径。
def execute(request):
    if request.get('protocol')!=1:raise ValueError('Unknown worker protocol')
    operation=request['op']
    if operation not in ('compile','disassemble','assemble'):raise ValueError('Unknown worker operation')
    if operation=='assemble':
        from .dxil_assembly import DxilAssembly
        compiler=DxilAssembly(request['dll'])
    else:
        compiler=Dxc(request['dll'])
    try:
        if operation=='compile':
            payload,warnings=compiler.compile(request['source'],request['entry'],request['profile'])
            return dict(payload=base64.b64encode(payload).decode('ascii'),warnings=warnings,ir=compiler.disassemble(payload))
        if operation=='assemble':
            payload,assembly_messages=compiler.assemble(request['ir'])
            payload,validation_messages=compiler.validate(payload)
            return dict(payload=base64.b64encode(payload).decode('ascii'),
                        warnings=assembly_messages+validation_messages,ir=compiler.disassemble(payload))
        payload=base64.b64decode(request['payload'],validate=True)
        return dict(ir=compiler.disassemble(payload))
    finally:compiler.close()


def main():
    if os.name=='nt':
        import ctypes
        # Report a native fault to the parent instead of displaying a blocking
        # Windows fault dialog. This setting applies only to this worker.
        ctypes.windll.kernel32.SetErrorMode(0x0001|0x0002|0x8000)
    try:
        request=json.loads(sys.stdin.buffer.read())
        result=dict(protocol=1,ok=True,result=execute(request))
    except Exception as exc:
        result=dict(protocol=1,ok=False,error=str(exc) or type(exc).__name__)
    sys.stdout.buffer.write(json.dumps(result,ensure_ascii=True).encode('utf-8'))
    sys.stdout.buffer.flush()


if __name__=='__main__':main()
