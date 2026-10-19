"""Loop state transitions, lane masks and real skin waterfall reconstruction."""
from pathlib import Path
import re
import unittest

from mmtr_editor.control_flow import single_natural_loop
from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.model import interface_chunks
from mmtr_editor.reconstruct import ReconstructionError, reconstruct_hlsl
from mmtr_editor.verify_ir import compare_dataflow

IR = '''define void @main() {
  %a = call i32 @dx.op.loadInput.i32(i32 4, i32 0, i32 0, i8 0, i32 undef)
  %v = call float @dx.op.loadInput.f32(i32 4, i32 1, i32 0, i8 0, i32 undef)
  br label %header
header:
  %old = phi float [ undef, %0 ], [ %next, %latch ]
  %pending = phi i32 [ 1, %0 ], [ %updated, %latch ]
  %done = icmp eq i32 %pending, 0
  br i1 %done, label %latch, label %group
group:
  %key = call i32 @dx.op.waveReadLaneFirst.i32(i32 118, i32 %a)
  %same = icmp eq i32 %key, %a
  br i1 %same, label %compute, label %latch
compute:
  %value = fmul float %v, 2.000000e+00
  br label %latch
latch:
  %next = phi float [ %old, %header ], [ %old, %group ], [ %value, %compute ]
  %updated = phi i32 [ 0, %header ], [ %pending, %group ], [ 0, %compute ]
  %more = icmp ne i32 %updated, 0
  %count = call i32 @dx.op.waveAllOp(i32 135, i1 %more)
  %finished = icmp eq i32 %count, 0
  br i1 %finished, label %exit, label %header
exit:
  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 0, float %next)
  ret void
}
'''


class LoopTransitionTests(unittest.TestCase):
    def test_arbitrary_state_transition_is_independent_of_ssa_and_phi_order(self):
        old = '  %old = phi float [ undef, %0 ], [ %next, %latch ]\n'
        pending = '  %pending = phi i32 [ 1, %0 ], [ %updated, %latch ]\n'
        reordered = IR.replace(old + pending, pending + old)
        reordered = re.sub(r'%old\b', '%renamed', reordered)
        report = compare_dataflow(IR, reordered)
        self.assertTrue(report['equal'])
        self.assertEqual(report['loop_state_counts'], [2, 2])
        dotted = IR.replace('%header', '%header.').replace('header:', 'header.:')
        self.assertTrue(compare_dataflow(IR, dotted)['equal'])

    def test_initial_feedback_exit_and_wave_changes_are_detected(self):
        for mutation in (
            IR.replace('[ 1, %0 ]', '[ 0, %0 ]'),
            IR.replace('[ undef, %0 ]', '[ 0.000000e+00, %0 ]'),
            IR.replace('[ %old, %group ]', '[ 0.000000e+00, %group ]'),
            IR.replace('[ 0, %compute ]', '[ 1, %compute ]'),
            IR.replace('icmp eq i32 %count', 'icmp ne i32 %count'),
            IR.replace('i32 118, i32 %a', 'i32 118, i32 17'),
            IR.replace('float %v, 2.000000e+00', 'float %v, 3.000000e+00'),
        ):
            with self.subTest(mutation=mutation):
                self.assertNotEqual(IR, mutation)
                self.assertFalse(compare_dataflow(IR, mutation)['equal'])

    def test_discard_on_a_later_iteration_is_compared(self):
        # pending starts at one, so a first-iteration-only check would miss
        # this discard when a lane has finished but other groups remain.
        mutation = IR.replace('  br i1 %done,',
                              '  call void @dx.op.discard(i32 82, i1 %done)\n  br i1 %done,')
        report = compare_dataflow(IR, mutation)
        self.assertFalse(report['equal'])
        self.assertIn('loop_next_discard_mask', [d['kind'] for d in report['differences']])

    def test_loop_wave_cannot_move_across_discard(self):
        wave = '  %key = call i32 @dx.op.waveReadLaneFirst.i32(i32 118, i32 %a)\n'
        discard = '  call void @dx.op.discard(i32 82, i1 true)\n'
        report = compare_dataflow(IR.replace(wave, discard + wave), IR.replace(wave, wave + discard))
        self.assertFalse(report['equal'])
        self.assertIn('wave_discard_masks', [d['kind'] for d in report['differences']])

    def test_unsafe_loop_writes_and_extra_exits_are_rejected(self):
        atomic = ('  %atomic = call i32 @dx.op.atomicBinOp.i32(i32 78, %dx.types.Handle undef, '
                  'i32 6, i32 0, i32 undef, i32 undef, i32 7)\n')
        output = '  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 0, float %v)\n'
        for mutation in (IR.replace('compute:\n', 'compute:\n' + atomic),
                         IR.replace('compute:\n', 'compute:\n' + output),
                         IR.replace('label %compute, label %latch', 'label %compute, label %exit')):
            with self.assertRaises(ReconstructionError):
                compare_dataflow(mutation, mutation)
        nested = {'0': ['br label %outer'],
                  'outer': ['br label %inner'],
                  'inner': ['br i1 true, label %inner, label %latch'],
                  'latch': ['br i1 true, label %outer, label %exit'], 'exit': ['ret void']}
        with self.assertRaises(ReconstructionError):
            single_natural_loop(nested)


FIXTURES = Path('output/skin_runtime/shaders')
DLL = Path('C:/Software/Kiro/dxcompiler.dll')


@unittest.skipUnless(DLL.exists() and (FIXTURES/'0390_PS_GBuffer.ll').exists(),
                     'Skin loop fixtures/DXC unavailable')
class SkinLoopRoundtripTests(unittest.TestCase):
    def test_four_real_loop_entries_compile_and_preserve_state_and_lane_masks(self):
        compiler = Dxc(DLL)
        try:
            for index in range(390, 394):
                with self.subTest(index=index):
                    path = FIXTURES/f'{index:04d}_PS_GBuffer.ll'
                    original = path.read_text(encoding='utf-8')
                    result = reconstruct_hlsl(original)
                    self.assertIsNotNone(result['natural_loop'])
                    self.assertIn('[loop] while (true)', result['source'])
                    raw, _ = compiler.compile(result['source'], result['entry'], result['profile'])
                    actual = compiler.disassemble(raw)
                    self.assertEqual(interface_chunks(raw), interface_chunks(path.with_suffix('.dxbc').read_bytes()))
                    self.assertFalse(reflection_differences(original, actual))
                    report = compare_dataflow(original, actual)
                    self.assertTrue(report['equal'])
                    self.assertEqual(report['loop_state_counts'], [16, 16])
                    self.assertEqual(report['wave_operation_counts'], [3, 3])
        finally:
            compiler.close()


if __name__ == '__main__':
    unittest.main()
