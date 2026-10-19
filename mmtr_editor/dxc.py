"""Optional Windows DXC bridge: compile user supplied HLSL; never execute shaders."""
import ctypes
import re
from inspect_mmtr import DxcBuffer, DxcDisassembler, guid, reflection


class Dxc(DxcDisassembler):
    def _result_blob(self, result, index):
        blob = ctypes.c_void_p()
        try:
            self.check(self.method(result, index, ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p))(
                result, ctypes.byref(blob)))
            if not blob:
                return b''
            pointer = self.method(blob, 3, ctypes.c_void_p)(blob)
            size = self.method(blob, 4, ctypes.c_size_t)(blob)
            return ctypes.string_at(pointer, size)
        finally:
            self.release(blob)

    def compile(self, source, entry, profile):
        if not entry or not profile:
            raise ValueError('请填写入口名称和目标 profile。')
        raw = source.encode('utf-8')
        storage = ctypes.create_string_buffer(raw)
        buffer = DxcBuffer(ctypes.cast(storage, ctypes.c_void_p), len(raw), 65001)
        args = ['-E', entry, '-T', profile, '-HV', '2021', '-O3']
        if re.search(r'^#define MMTR_NATIVE_16BIT_TYPES 1\s*$',source,re.M):
            model = re.fullmatch(r'(?:ps|vs|cs|ms|as|lib)_6_(\d+)',profile)
            if model is None or int(model[1]) < 2:
                raise ValueError('原生16位类型需要Shader Model 6.2或更高。')
            args.append('-enable-16bit-types')
        arguments = (ctypes.c_wchar_p * len(args))(*args)
        result = ctypes.c_void_p()
        try:
            self.check(self.method(self.compiler, 3, ctypes.c_int32, ctypes.POINTER(DxcBuffer),
                                   ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_uint32, ctypes.c_void_p,
                                   ctypes.POINTER(type(guid('00000000-0000-0000-0000-000000000000'))),
                                   ctypes.POINTER(ctypes.c_void_p))(
                self.compiler, ctypes.byref(buffer), arguments, len(args), None,
                ctypes.byref(guid('58346cda-dde7-4497-9461-6f87af5e0659')), ctypes.byref(result)))
            status = ctypes.c_int32()
            self.check(self.method(result, 3, ctypes.c_int32, ctypes.POINTER(ctypes.c_int32))(
                result, ctypes.byref(status)))
            errors = self._result_blob(result, 5).rstrip(b'\0').decode('utf-8', errors='replace')
            if status.value < 0:
                raise ValueError(errors or f'DXC 编译失败：{status.value:#x}')
            return self._result_blob(result, 4), errors
        finally:
            self.release(result)


def reflection_differences(old_ir, new_ir):
    """Conservative reflection comparison; no claim of complete GPU validation."""
    old, new = reflection(old_ir), reflection(new_ir)
    differences = []
    if old['resource_binding_rows'] != new['resource_binding_rows']:
        differences.append('资源绑定反射')
    if old['cbuffers'] != new['cbuffers']:
        differences.append('常量缓冲反射布局')
    if old['structured_buffers'] != new['structured_buffers']:
        differences.append('结构化缓冲反射布局')
    return differences
