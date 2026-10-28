"""删除只改归属到 MDF 布局的 guard；覆盖保留行填充、地址收缩和严格拒绝。"""
from dataclasses import replace
import unittest

from mmtr_editor.material_addresses import MaterialTailLayout, material_reads
from mmtr_editor.material_markers import marker_offset, marker_value, material_marker_reads
from mmtr_editor.material_removal import plan_removal_layout, prove_shader_removal, remap_shader_reads
from mmtr_editor.model import EditError
from test_material_addresses import cb_ir
from test_material_removal import TEXTURE_IR


def guarded(layout, components, *, raw=False, comparison='eq'):
    offset = marker_offset(layout)
    if raw:
        source = TEXTURE_IR.replace('%base, 32', f'%base, {offset//16}')
        source = source[:source.index('  %handle =')]+f'  %guard = icmp {comparison} i32 %v, {marker_value(layout, components)}\n  ret void\n}}\n'
        return source
    return cb_ir(offset//16, (0,)).replace('  ret void',
        f'  %unrelated = add i32 %descriptor, {marker_value(layout, components)}\n'
        f'  %guard = icmp {comparison} i32 %field0, {marker_value(layout, components)}\n  ret void')


class RemovalMarkerTests(unittest.TestCase):
    def check(self, source, plan):
        proof = prove_shader_removal(source, plan)
        self.assertEqual(proof['material_markers'], 1)
        edited, report = remap_shader_reads(source, plan)
        _, guards = material_marker_reads(edited, plan.new, plan.new_components, material_reads(edited))
        self.assertEqual(len(guards), 1)
        self.assertEqual(guards[0]['read']['extracts'][0]['offset'], marker_offset(plan.new))
        self.assertEqual(report['functions'][0]['material_markers'], 1)
        return edited

    def test_component_count_changes_while_parameter_block_and_marker_offset_stay(self):
        old = MaterialTailLayout(16, ('Map',), ())
        props = [dict(name='Live', offset=0, byte_size=4, count=1),
                 dict(name='Dead', offset=4, byte_size=4, count=1)]
        plan = plan_removal_layout(old, props, [dict(action='parameter', name='Dead')])
        self.assertEqual(plan.new, old)
        self.assertEqual((plan.old_components, plan.new_components), (2, 1))
        edited = self.check(guarded(old, 2), plan)
        self.assertIn('%unrelated = add i32 %descriptor, 1026', edited)
        self.assertIn('icmp eq i32 %field0, 1025', edited)

    def test_compaction_and_texture_deletion_migrate_raw_and_cb_guards(self):
        old = MaterialTailLayout(32, ('Map', 'DeadMap'), ('GP',))
        props = [dict(name='Live', offset=0, byte_size=16), dict(name='Dead', offset=16, byte_size=16)]
        for actions in ([dict(action='parameter', name='Dead')], [dict(action='texture', name='DeadMap')],
                        [dict(action='parameter', name='Dead'), dict(action='texture', name='DeadMap')]):
            plan = plan_removal_layout(old, props, actions)
            for raw in (False, True):
                for comparison in ('eq', 'ne'):
                    with self.subTest(actions=actions, raw=raw, comparison=comparison):
                        self.check(guarded(old, 8, raw=raw, comparison=comparison), plan)

    def test_unknown_footer_wrong_count_extra_consumer_and_same_offset_alias_reject(self):
        old = MaterialTailLayout(16, ('Map',), ())
        props = [dict(name='Live', offset=0, byte_size=4), dict(name='Dead', offset=4, byte_size=4)]
        plan = plan_removal_layout(old, props, [dict(action='parameter', name='Dead')])
        source = guarded(old, 2)
        invalid = [source.replace('i32 %field0, 1026', 'i32 %field0, 1027'),
                   source.replace('i32 2)', 'i32 3)'),
                   source.replace('  ret void', '  %extra = add i32 %field0, 1\n  ret void')]
        alias = source.replace('  ret void',
            '  %alias = call %dx.types.CBufRet.i32 @dx.op.cbufferLoadLegacy.i32(i32 59, %dx.types.Handle %cb, i32 2)\n'
            '  %aliasv = extractvalue %dx.types.CBufRet.i32 %alias, 0\n'
            '  %unknown = add i32 %aliasv, 1\n  ret void')
        for ir in invalid+[alias]:
            with self.subTest(ir=ir), self.assertRaises(EditError):
                prove_shader_removal(ir, plan)
        self.assertNotEqual(plan.digest, replace(plan, old_components=3).digest)
        with self.assertRaisesRegex(EditError, '分量数'):
            plan_removal_layout(old, [{**props[0], 'count': 2}, props[1]], [dict(action='parameter', name='Dead')])


if __name__ == '__main__':
    unittest.main()
