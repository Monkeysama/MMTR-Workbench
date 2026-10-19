"""Migration diagnostics must expose lost inputs and never rewrite a template."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import struct

from mmtr_editor.mdf import MdfDocument, MdfError
from mmtr_editor.migration import build_migration_report
from mmtr_editor.rpc import Service
from tests.local_corpus import ORIGINAL_TOP_MDF as MDF, verify_original_top_mdf

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'output/pix/detailcol_reference'


def member(name, offset=0, size=4):
    return dict(name=name, byte_offset=offset, byte_size=size)


def info(members, srvs=(), source='MaterialShader/Variation/Source.mmtr.240906211'):
    return dict(source=source, sha256='fixture', metadata=dict(
        cbuffers=[dict(name='UserMaterial', buffer_size=256, members=members)],
        srvs=[dict(name=name) for name in srvs]))


def material(properties=(), textures=()):
    return dict(index=6, name='Top', master='MaterialShader/Variation/Source.mmtr',
        properties=[dict(name=name, offset=offset, byte_size=size, values=[0.25], raw_hex='0000803e') for name, offset, size in properties],
        textures=[dict(name=name, path=f'Texture/{name}') for name in textures])


class MigrationClassificationTests(unittest.TestCase):
    def test_all_parameter_categories_preserve_source_evidence_without_mutation(self):
        names = ['Same', 'Moved', 'Wide', 'Missing', 'BadBinding', 'Lost']
        source = info([member(name, i * 16) for i, name in enumerate(names)])
        target = info([member('Same'), member('Moved', 128), member('Wide', 32, 16),
            member('Missing', 48), member('BadBinding', 64), member('New', 144), member('CAPCOM_MATERIAL_RESERVE0', 160)])
        mdf = material([('Same', 0, 4), ('Moved', 16, 4), ('Wide', 32, 4), ('BadBinding', 68, 4), ('Lost', 80, 4)])
        before = copy.deepcopy((mdf, source, target))
        report = build_migration_report(mdf, source, target)
        self.assertEqual((mdf, source, target), before)
        rows = {row['name']: row for row in report['property_mapping']}
        self.assertEqual({name: row['status'] for name, row in rows.items()}, dict(
            Same='copy_in_place', Moved='copy_requires_repack', Wide='width_conflict', Missing='source_value_missing',
            BadBinding='source_binding_mismatch', New='target_default_required', CAPCOM_MATERIAL_RESERVE0='reserve_value_unspecified'))
        self.assertEqual(rows['Wide']['source_byte_size'], 4)
        self.assertEqual(rows['Wide']['byte_size'], 16)
        self.assertEqual(rows['Moved']['source_values'], [0.25])
        self.assertEqual(rows['Moved']['source_raw_hex'], '0000803e')
        self.assertEqual([row['name'] for row in report['source_only_properties']], ['Lost'])
        self.assertTrue(report['structural_mdf_repack_required'])
        for key in report['blockers']:
            self.assertIn(key, report['status_catalog'])
        self.assertFalse(report['safe_to_auto_migrate'])
        self.assertFalse(report['semantic_equivalence_verified'])

    def test_global_srvs_are_not_inferred_as_missing_material_textures(self):
        report = build_migration_report(material(textures=['BaseMap', 'LostMap', 'UnknownMap']),
            info([], ['BaseMap', 'LostMap', 'SceneDepth']), info([], ['BaseMap', 'ShadowMap', 'NewEngineResource']))
        self.assertEqual({row['name']: row['status'] for row in report['texture_mapping']}, dict(
            BaseMap='copy_slot', LostMap='source_texture_not_supported', UnknownMap='source_mdf_slot_not_in_source_mmtr'))
        self.assertEqual(report['texture_mapping'][0]['source_paths'], ['Texture/BaseMap'])
        self.assertEqual(report['target_material_texture_requirements'], 'unknown_until_target_mdf_or_shader_binding_analysis')
        self.assertNotIn('target_texture_required', report['summary'])
        self.assertIn('NewEngineResource', report['engine_resource_changes']['added_srvs'])

    def test_reserve_like_names_do_not_receive_invented_defaults(self):
        report = build_migration_report(material(), info([]), info([member('CAPCOM_MATERIAL_RESERVE_NotReserved')]))
        self.assertEqual(report['property_mapping'][0]['status'], 'target_default_required')

    def test_mdf_properties_absent_from_source_are_not_silently_dropped(self):
        report = build_migration_report(material([('Extra', 0, 4)]), info([]), info([]))
        self.assertEqual(report['extra_mdf_properties'][0]['name'], 'Extra')
        self.assertIn('mdf_property_not_in_source', report['blockers'])

    def test_duplicate_names_and_ambiguous_layouts_are_rejected(self):
        base = info([member('Same')])
        with self.assertRaises(MdfError):
            build_migration_report(material([('Same', 0, 4), ('Same', 16, 4)]), base, base)
        duplicate = info([member('Same'), member('Same', 16)])
        ambiguous = copy.deepcopy(base)
        ambiguous['metadata']['cbuffers'].append(dict(name='UserMaterial', buffer_size=32, members=[]))
        for target in [duplicate, ambiguous, dict(metadata={})]:
            with self.subTest(target=target), self.assertRaises(MdfError):
                build_migration_report(material(), base, target)

    def test_source_master_must_match_full_relative_path(self):
        for source in ['Elsewhere/Source.mmtr.240906211', 'MaterialShader/Variation/Source.mmtr.fake']:
            with self.subTest(source=source), self.assertRaises(MdfError):
                build_migration_report(material(), info([], source=source), info([]))
        report = build_migration_report(material(), info([], source='F:/EXTRACT/MaterialShader/Variation/Source.mmtr.240906211'), info([]))
        self.assertEqual(report['material_name'], 'Top')

    def test_identical_layout_is_still_only_a_semantic_candidate(self):
        base = info([member('Same')], ['BaseMap'])
        report = build_migration_report(material([('Same', 0, 4)], ['BaseMap']), base, base)
        self.assertEqual(report['blockers'], [])
        self.assertFalse(report['structural_mdf_repack_required'])
        self.assertFalse(report['safe_to_auto_migrate'])


@unittest.skipUnless(MDF.is_file() and CORPUS.is_dir(), 'local MHWILDS corpus unavailable')
class RealMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        verify_original_top_mdf()
        cls.source = next((CORPUS / 'archives').rglob('Base_ATOS_Emit_FX_Detail_DetailColLayer.mmtr.240906211'))
        cls.target = next((CORPUS / 'closest_native').rglob('*.mmtr.240906211'))

    def service(self):
        service = Service()
        service.handle(dict(op='mdf_open', path=str(MDF), material_index=6))
        return service

    def analyze(self, service):
        return service.handle(dict(op='mdf_migration', source_path=str(self.source), target_path=str(self.target)))

    def test_actual_top_mapping_and_export_leave_all_inputs_unchanged(self):
        inputs = [MDF, self.source, self.target]
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
        service = self.service()
        snapshot = self.analyze(service)
        report = snapshot['migration']
        self.assertIsNone(service.doc)
        self.assertFalse(snapshot['dirty'])
        self.assertEqual(report['shared_material_count'], 50)
        self.assertEqual(report['summary']['copy_in_place'], 1)
        self.assertEqual(report['summary']['copy_requires_repack'], 147)
        self.assertEqual(report['summary']['source_feature_not_in_target'], 18)
        self.assertEqual(report['summary']['source_texture_not_supported'], 5)
        self.assertEqual(report['summary']['copy_slot'], 17)
        self.assertEqual(len(report['property_mapping']), 173)
        self.assertEqual(len(report['texture_mapping']), 22)
        self.assertEqual(set(row['name'] for row in report['texture_mapping'] if row['status'] == 'source_texture_not_supported'),
            {'AlphaTranslucentOcclusionSSSMap', 'BaseDielectricMap', 'ColorLayer_DetailMaskMap', 'ColorLayer_MaskMap', 'VectorEmitMap'})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'migration.json'
            service.handle(dict(op='mdf_report', path=str(path)))
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['migration'], report)
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})

    def test_report_export_protects_mdf_and_both_mmtr_inputs(self):
        service = self.service()
        self.analyze(service)
        for path in [MDF, self.source, self.target]:
            with self.subTest(path=str(path)), self.assertRaisesRegex(ValueError, '不能覆盖'):
                service.handle(dict(op='mdf_report', path=str(path)))

    def test_selection_and_property_changes_invalidate_report(self):
        service = self.service()
        self.analyze(service)
        selected = service.handle(dict(op='mdf_select', material_index=7))
        self.assertIsNone(selected['migration'])
        service.handle(dict(op='mdf_select', material_index=6))
        self.analyze(service)
        edited = service.handle(dict(op='mdf_set_property', material_index=6, name='AlphaAdjust', values=[0.25]))
        self.assertIsNone(edited['migration'])
        fresh = self.analyze(service)['migration']
        alpha = next(row for row in fresh['property_mapping'] if row['name'] == 'AlphaAdjust')
        self.assertEqual(alpha['source_values'], [0.25])
        self.assertNotEqual(fresh['mdf']['sha256'], fresh['mdf']['source_sha256'])
        reset = service.handle(dict(op='mdf_reset'))
        self.assertIsNone(reset['migration'])
        self.assertFalse(reset['dirty'])

    def test_final_alignment_can_be_absent_but_real_property_data_cannot(self):
        data = MDF.read_bytes()
        parsed = MdfDocument(data)
        final = parsed.materials[-1]
        end = max(p['_absolute_offset'] + p['byte_size'] for p in final['properties'])
        self.assertEqual(end, len(data))
        self.assertEqual(parsed.build()[0], data)
        with self.assertRaises(MdfError):
            MdfDocument(data[:-1])
        oversized = bytearray(data)
        struct.pack_into('<I', oversized, final['offset'] + 12, final['property_block_size'] + 16)
        with self.assertRaises(MdfError):
            MdfDocument(bytes(oversized))


if __name__ == '__main__':
    unittest.main()
