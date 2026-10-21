import unittest

from mmtr_editor.effect_templates import effect_template
from mmtr_editor.model import EditError


class EffectTemplateTests(unittest.TestCase):
    def test_emission_template_is_explicit_and_non_destructive(self):
        report = effect_template('emission_multiplier')
        self.assertEqual([item['action'] for item in report['actions']], ['parameter', 'texture'])
        self.assertEqual(report['actions'][1]['path'], 'systems/rendering/NullBlack.tex')
        self.assertTrue(report['requires_compiled_shader'])
        self.assertEqual(report['state_editing'], 'unsupported')
        self.assertFalse(report['game_tested'])

    def test_unknown_template_rejected(self):
        with self.assertRaises(EditError):
            effect_template('arbitrary_graph')
