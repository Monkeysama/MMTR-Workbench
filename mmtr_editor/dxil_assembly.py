"""DXC assembler/validator bridge for controlled, local DXIL experiments."""
import ctypes
import struct
from inspect_mmtr import guid, decode_dxbc
from .dxc import Dxc


class DxilAssembly(Dxc):
    def create(self, clsid, iid):
        obj = ctypes.c_void_p()
        self.check(self.dll.DxcCreateInstance(ctypes.byref(guid(clsid)), ctypes.byref(guid(iid)), ctypes.byref(obj)))
        return obj

    def blob(self, raw):
        library = self.create('6245d6af-66e0-48fd-80b4-4d271796748c', 'e5204dc7-d18c-4c3c-bdfb-851673980fe7')
        storage = ctypes.create_string_buffer(raw)
        blob = ctypes.c_void_p()
        try:
            # IDxcLibrary::CreateBlobWithEncodingOnHeapCopy owns an independent copy.
            self.check(self.method(library, 7, ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32,
                                   ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))(
                library, storage, len(raw), 65001, ctypes.byref(blob)))
            return blob
        finally:
            self.release(library)

    def operation_result(self, result):
        status = ctypes.c_int32()
        self.check(self.method(result, 3, ctypes.c_int32, ctypes.POINTER(ctypes.c_int32))(result, ctypes.byref(status)))
        errors = self._result_blob(result, 5).rstrip(b'\0').decode('utf-8', errors='replace')
        if status.value < 0:
            raise ValueError(errors or f'DXIL operation failed: {status.value:#x}')
        return self._result_blob(result, 4), errors

    def assemble(self, ir):
        assembler = self.create('d728db68-f903-4f80-94cd-dccf76ec7151', '091f7a26-1c1f-4948-904b-e6e3a8a771d5')
        blob, result = self.blob(ir.encode('utf-8')), ctypes.c_void_p()
        try:
            self.check(self.method(assembler, 3, ctypes.c_int32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                assembler, blob, ctypes.byref(result)))
            return self.operation_result(result)
        finally:
            self.release(result)
            self.release(blob)
            self.release(assembler)

    def validate(self, container):
        validator = self.create('8ca3e215-f728-4cf3-8cdd-88af917587a1', 'a6e82bd2-1fd7-4826-9811-2857e797f49a')
        blob, result = self.blob(container), ctypes.c_void_p()
        try:
            self.check(self.method(validator, 3, ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32,
                                   ctypes.POINTER(ctypes.c_void_p))(validator, blob, 1, ctypes.byref(result)))
            return self.operation_result(result)
        finally:
            self.release(result)
            self.release(blob)
            self.release(validator)


def retain_reflection(container, original):
    """Preserve original STAT reflection for arithmetic-only edits with same bindings.

    Never use this for resource/layout/signature changes. The caller must compare
    interfaces and bindings and validate the final combined container.
    """
    old, new = decode_dxbc(original, 0), decode_dxbc(container, 0)
    stat = next((c for c in old['chunks'] if c['tag'] == 'STAT'), None)
    if stat is None:
        raise ValueError('Original reflection STAT missing')
    chunks = [container[c['offset']:c['offset']+8+c['size']] for c in new['chunks'] if c['tag'] != 'STAT']
    chunks.append(original[stat['offset']:stat['offset']+8+stat['size']])
    cursor = 32 + 4 * len(chunks)
    header = bytearray(container[:32])
    header[4:20] = b'\0' * 16
    struct.pack_into('<I', header, 28, len(chunks))
    for raw in chunks:
        header.extend(struct.pack('<I', cursor))
        cursor += len(raw)
    struct.pack_into('<I', header, 24, cursor)
    return bytes(header) + b''.join(chunks)
