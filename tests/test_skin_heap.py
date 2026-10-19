"""Real instanced/heap skin entries and counterexamples to the verification rules."""
from pathlib import Path
import re
import unittest
from mmtr_editor.reconstruct import reconstruct_hlsl, ReconstructionError
from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.model import interface_chunks
from mmtr_editor.verify_ir import compare_dataflow, Graph

ROOT = Path('output/skin_runtime/shaders')
DLL = Path('C:/Software/Kiro/dxcompiler.dll')


@unittest.skipUnless((ROOT/'0228_PS_GBuffer.ll').exists() and DLL.exists(), 'Skin runtime fixtures/DXC unavailable')
class SkinHeapTests(unittest.TestCase):
    def test_instanced_and_observed_heap_entries_compile_with_complete_dataflow(self):
        x = Dxc(DLL)
        try:
            for index in (66, 228):
                with self.subTest(index=index):
                    fixture = ROOT/f'{index:04d}_PS_GBuffer.ll'
                    ir = fixture.read_text(encoding='utf-8')
                    r = reconstruct_hlsl(ir)
                    raw, _ = x.compile(r['source'], r['entry'], r['profile'])
                    compiled = x.disassemble(raw)
                    self.assertEqual(interface_chunks(raw), interface_chunks(fixture.with_suffix('.dxbc').read_bytes()))
                    self.assertFalse(reflection_differences(ir, compiled))
                    self.assertTrue(compare_dataflow(ir, compiled)['equal'])
                    self.assertIn('StructuredBuffer<InstanceWorld>', r['source'])
                    if index == 228:
                        self.assertIn('ResourceDescriptorHeap[NonUniformResourceIndex(', r['source'])
                        self.assertIn('SV_ShadingRate', r['source'])
                        self.assertIn('InterlockedMin', r['source'])
                        self.assertIn('registers[2048]', r['source'])
        finally:
            x.close()

    def test_structured_stride_field_layout_and_scalar_element_changes_are_rejected(self):
        ir = (ROOT/'0228_PS_GBuffer.ll').read_text(encoding='utf-8')
        for changed in (ir.replace('!7 = !{i32 1, i32 128}', '!7 = !{i32 1, i32 132}', 1),
                        ir.replace('uint userParam;                               ; Offset:  124', 'uint userParam;                               ; Offset:  120', 1),
                        ir.replace('uint $Element;', 'float $Element;', 1)):
            self.assertNotEqual(ir, changed)
            with self.assertRaises(ReconstructionError):
                reconstruct_hlsl(changed)
        changed = ir.replace('uint $Element;', 'float $Element;', 1)
        self.assertIn('结构化缓冲反射布局', reflection_differences(ir, changed))

    def test_atomic_effect_and_descriptor_index_mutations_detected(self):
        ir = (ROOT/'0228_PS_GBuffer.ll').read_text(encoding='utf-8')
        mutations = (ir.replace('i32 6, i32 %2469', 'i32 7, i32 %2469', 1),
                     ir.replace('i32 6, i32 %2469', 'i32 6, i32 0', 1),
                     ir.replace('i32 218, i32 %159', 'i32 218, i32 0', 1),
                     # -1 is outside the 16-bit mask; 17 is not. It cannot be removed.
                     ir.replace('icmp ne i32 %353, -1', 'icmp ne i32 %353, 17', 1))
        for changed in mutations:
            self.assertNotEqual(ir, changed)
            self.assertFalse(compare_dataflow(ir, changed)['equal'])

    def test_additional_atomic_and_reads_after_atomic_are_not_treated_as_pure(self):
        ir = (ROOT/'0228_PS_GBuffer.ll').read_text(encoding='utf-8')
        atomic = re.search(r'^  %2471 = call i32 @dx.op.atomicBinOp.i32\([^\n]+', ir, re.M)[0]
        duplicate = ir.replace(atomic, atomic + '\n' + atomic.replace('%2471 =', '%999998 ='), 1)
        after_read = ir.replace(atomic, atomic + '\n  %999999 = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %17, i32 0, i32 undef, i8 1, i32 4)', 1)
        for changed in (duplicate, after_read):
            with self.assertRaises(ReconstructionError):
                compare_dataflow(ir, changed)

    def test_undefined_structured_lane_not_normalized(self):
        ir = (ROOT/'0066_PS_GBuffer.ll').read_text(encoding='utf-8')
        changed = ir.replace('i32 %47, i32 0, i8 15, i32 4)', 'i32 %47, i32 0, i8 1, i32 4)', 1)
        self.assertNotEqual(ir, changed)
        with self.assertRaises(ReconstructionError):
            compare_dataflow(ir, changed)

    def test_compiled_hlsl_tint_changes_only_two_color_components_and_keeps_uav_effect(self):
        folder = Path('output/skin_game_test/hlsl_228')
        if not (folder/'H_hlsl_tint.ll').exists():
            self.skipTest('Prepare G/H artifacts first')
        original = (ROOT/'0228_PS_GBuffer.ll').read_text(encoding='utf-8')
        baseline = (folder/'G_hlsl_baseline.ll').read_text(encoding='utf-8')
        tinted = (folder/'H_hlsl_tint.ll').read_text(encoding='utf-8')
        oracle = (folder/'tint_reference.ll').read_text(encoding='utf-8')
        self.assertTrue(compare_dataflow(original, baseline)['equal'])
        self.assertTrue(compare_dataflow(oracle, tinted)['equal'])
        self.assertFalse(compare_dataflow(original, tinted)['equal'])
        def effects(ir):
            graph = Graph(ir)
            result = {}
            for guard, key in graph.observables()['effects']:
                node = graph.nodes[key]
                slot = (tuple(graph.nodes[x][1] for x in node[4][1:4])
                        if node[2].startswith('storeOutput.') else 'atomic')
                result[slot] = guard, key
            return result
        before, after = effects(original), effects(tinted)
        self.assertEqual(before.keys(), after.keys())
        self.assertEqual({key for key in before if before[key] != after[key]}, {(1, 0, 1), (1, 0, 2)})
        self.assertIn('atomic', before)


if __name__ == '__main__':
    unittest.main()
