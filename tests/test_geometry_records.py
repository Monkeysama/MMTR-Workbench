"""几何记录读取（RE9 光追/meshlet）与删除调用白名单的合成测试；不依赖 DXC 或游戏资产。

形态取自 RE9 原版 RayTraceShading 与 meshlet 计算 Shader：BINDLESSINFOSRV.geometryByteOffset / Instances.geometry
给出几何记录在 BindlessBuffer 中的地址或 heap 下标，这些读取与材质数据共用 BindlessBuffer 但不是材质字段。
"""
import unittest

from mmtr_editor.material_addresses import geometry_fields, material_reads
from mmtr_editor.material_removal import shader_removal_reads
from mmtr_editor.material_usage import shader_input_reads

REFLECTION = '''; Resource bind info for BindlessBuffer
; {
;
;   uint4 $Element;                                   ; Offset:    0 Size:    16
;
; }
;
; Resource bind info for BINDLESSINFOSRV
; {
;
;   struct struct.InstanceData
;   {
;
;       uint materialByteOffset;                      ; Offset:    0
;       uint FIELDNAME;                      ; Offset:    4
;
;   } $Element;                                       ; Offset:    0 Size:    32
;
; }
;
; Resource Bindings:
;
; Name                                 Type  Format         Dim      ID      HLSL Bind  Count
; BindlessBuffer                    texture  struct         r/o      T0             t0     1
; BINDLESSINFOSRV                   texture  struct         r/o      T1             t1     1
'''

BODY = '''define void @main() {
  %bb = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 0, i32 0, i32 0, i1 false)
  %info = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 0, i32 1, i32 1, i1 false)
  %gl = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %info, i32 %i, i32 4, i8 1, i32 4)
  %geo = extractvalue %dx.types.ResRet.i32 %gl, 0
  %ga = add i32 %geo, 32
  %gr = lshr i32 %ga, 4
  %grow = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %bb, i32 %gr, i32 0, i8 15, i32 4)
  %vb = extractvalue %dx.types.ResRet.i32 %grow, 1
  %vh = call %dx.types.Handle @dx.op.createHandleFromHeap(i32 218, i32 %vb, i1 false, i1 true)
  %va = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %vh, %dx.types.ResourceProperties { i32 11, i32 0 })
  %vertex = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %va, i32 %v, i32 undef, i8 15, i32 4)
  %vx = extractvalue %dx.types.ResRet.i32 %vertex, 0
  %ml = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %info, i32 %i, i32 0, i8 1, i32 4)
  %mat = extractvalue %dx.types.ResRet.i32 %ml, 0
  %mr = lshr i32 %mat, 4
  %mrow = add i32 %mr, 2
  %load = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %bb, i32 %mrow, i32 0, i8 15, i32 4)
  %param = extractvalue %dx.types.ResRet.i32 %load, 1
  %use = add i32 %param, %vx
  ret void
}
'''


def ir(field='geometryByteOffset'):
    return (REFLECTION + BODY).replace('FIELDNAME', field)


class GeometryRecordTests(unittest.TestCase):
    def test_reflection_names_only_geometry_fields(self):
        self.assertEqual(geometry_fields(ir()), {'BINDLESSINFOSRV': {4}})
        self.assertEqual(geometry_fields(ir('vertexCount')), {})

    def test_geometry_rows_are_not_material_reads(self):
        reads = material_reads(ir(), header_bytes=0)
        self.assertEqual([(r['ssa'], r['register']) for r in reads], [('%load', 2)])
        self.assertEqual(reads[0]['extracts'], [{'ssa': '%param', 'lane': 1, 'offset': 36}])

    # 字段未被反射命名为几何时不猜测：该行仍按材质读取识别（与本次改动前一致）。
    def test_unnamed_field_keeps_previous_classification(self):
        reads = material_reads(ir('vertexCount'), header_bytes=0)
        self.assertEqual(sorted(r['ssa'] for r in reads), ['%grow', '%load'])

    def test_geometry_heap_buffer_is_covered_but_other_heap_reads_are_not(self):
        self.assertEqual(shader_input_reads(ir())['errors'], [])
        # 下标来自材质字段（GPBF 槽）时读的是 MDF 引用的资源内容，覆盖完整且该字段记为资源句柄。
        material_index = ir().replace('i32 218, i32 %vb,', 'i32 218, i32 %param,')
        reads = shader_input_reads(material_index)
        self.assertEqual(reads['errors'], [])
        self.assertIn(36, reads['record_handles'])
        # 下标来源既非几何也非材质字段（如算术结果）时仍未覆盖。
        derived = ir().replace('i32 218, i32 %vb,', 'i32 218, i32 %use,')
        self.assertTrue(any('缓冲读取来源' in error for _, error in shader_input_reads(derived)['errors']))


class RemovalCallWhitelistTests(unittest.TestCase):
    LIFETIME = ir().replace('  ret void', '  %arr = alloca [4 x i32], align 4\n'
                            '  %raw = bitcast [4 x i32]* %arr to i8*\n'
                            '  call void @llvm.lifetime.start(i64 16, i8* %raw) #0\n'
                            '  call void @llvm.lifetime.end(i64 16, i8* %raw) #0\n'
                            '  call void @llvm.lifetime.start(i64 12, i8* bitcast ([3 x float]* @.hca.1 to i8*)) #0\n'
                            '  ret void')

    def test_lifetime_markers_are_not_unproven_calls(self):
        errors = [error for _, error in shader_removal_reads(self.LIFETIME)['errors']]
        self.assertFalse(any('未证明的函数调用' in error for error in errors), errors)

    def test_other_calls_remain_blocked(self):
        other = self.LIFETIME.replace('@llvm.lifetime.end(i64 16, i8* %raw)', '@helper(i64 16, i8* %raw)')
        errors = [error for _, error in shader_removal_reads(other)['errors']]
        self.assertTrue(any('未证明的函数调用' in error for error in errors))


if __name__ == '__main__':
    unittest.main()
