"""文档缓存失效及回滚测试；合成状态隔离昂贵构建，验证值变化不能复用旧候选。"""
import copy
import hashlib
from pathlib import Path
import unittest
import tempfile
from unittest.mock import Mock, patch

from mmtr_editor.bounded_cache import ByteLRU
from mmtr_editor.model import Document


class ByteLRUTests(unittest.TestCase):
    def test_tracks_byte_and_entry_limits_with_lru_order(self):
        cache = ByteLRU(2, 8)
        cache.put('a', 'A', 4)
        cache.put('b', 'B', 4)
        self.assertEqual(cache.get('a'), 'A')
        cache.put('c', 'C', 4)
        self.assertIsNone(cache.get('b'))
        self.assertEqual((cache.get('a'), cache.get('c')), ('A', 'C'))
        self.assertEqual((len(cache), cache.byte_size), (2, 8))

    def test_replacement_and_oversized_entries_do_not_retain_stale_bytes(self):
        cache = ByteLRU(2, 8)
        cache.put('a', 'A', 6)
        cache.put('a', 'AA', 3)
        self.assertEqual(cache.byte_size, 3)
        cache.put('too-large', 'B', 9)
        self.assertIsNone(cache.get('too-large'))
        self.assertEqual((len(cache), cache.byte_size), (1, 3))

    def test_deepcopy_has_independent_eviction_state(self):
        cache = ByteLRU(2, 8)
        cache.put('a', 'A', 4)
        cache.put('b', 'B', 4)
        copied = copy.deepcopy(cache)
        copied.get('a')
        copied.put('c', 'C', 4)
        self.assertIsNone(copied.get('b'))
        self.assertEqual(cache.get('b'), 'B')
        self.assertEqual((len(cache), len(copied)), (2, 2))


# 绕过文件读取只测试缓存边界；真实重建字节在本地MCP语料验收中独立对照。
def document():
    doc = Document.__new__(Document)
    doc.source = Path('synthetic.mmtr')
    doc.original = b'original'
    doc.info = {'sha256': 'original'}
    doc.replacements = {0: b'edited'}
    doc.renames, doc.redirects, doc.metadata_edits, doc.replacement_notes = {}, {}, [], {}
    doc.integrity = {'name_hash_mismatches': [], 'shader_trailer_mismatches': []}
    doc._build_uncached = Mock(side_effect=lambda: (b'candidate', {'metadata_edits': copy.deepcopy(doc.metadata_edits)},
                                                   {'metadata': copy.deepcopy(doc.metadata_edits)}))
    return doc


class CompileCacheTests(unittest.TestCase):
    def test_compile_cache_hits_only_until_dxc_content_changes(self):
        from mmtr_editor.rpc import Service
        with tempfile.TemporaryDirectory() as folder:
            dll = Path(folder) / 'dxcompiler.dll'
            dll.write_bytes(b'first identity')
            service = Service()
            request = {'op': 'compile', 'dll': str(dll), 'source': 'float4 main() : SV_Target { return 0; }',
                       'entry': 'main', 'profile': 'ps_6_6'}
            with patch('mmtr_editor.rpc.compile_hlsl', return_value=(b'payload', [], 'ir')) as compiler:
                service.handle(request)
                service.handle(request)
                self.assertEqual(compiler.call_count, 1)
                dll.write_bytes(b'second identity')
                service.handle(request)
                self.assertEqual(compiler.call_count, 2)
                source_hash = hashlib.sha256(request['source'].encode()).hexdigest()
                self.assertEqual(service.compile_cache.get((
                    str(dll.resolve()), hashlib.sha256(b'second identity').hexdigest(),
                    source_hash, 'main', 'ps_6_6')), (b'payload', [], 'ir'))

    def test_compile_identity_change_discards_candidate_and_cache(self):
        from mmtr_editor.rpc import Service
        with tempfile.TemporaryDirectory() as folder:
            dll = Path(folder) / 'dxcompiler.dll'
            dll.write_bytes(b'first identity')
            service = Service()

            def compile_and_replace(*args):
                dll.write_bytes(b'second identity')
                return b'payload', [], 'ir'

            with patch('mmtr_editor.rpc.compile_hlsl', side_effect=compile_and_replace):
                with self.assertRaisesRegex(Exception, 'DXC 内容已改变'):
                    service.handle({'op': 'compile', 'dll': str(dll), 'source': 'bad',
                                    'entry': 'main', 'profile': 'ps_6_6'})
            self.assertIsNone(service.compiled)
            self.assertEqual(len(service.compile_cache), 0)


class BuildCacheTests(unittest.TestCase):
    def test_worker_reflection_reuse_requires_matching_shader_hash(self):
        doc = document()
        doc.metadata_edits = [{'kind': 'add_cbuffer_member', 'cbuffer': 'Other'}]
        doc.info.update(shaders=[{'index': 0, 'offset': 0, 'trailer_offset': 8}], records=[],
                        metadata={'cbuffers': [], 'srvs': [], 'samplers': [], 'uavs': []})
        reflected = {'cbuffers': [], 'resource_binding_rows': []}
        for digest, expected_calls in ((hashlib.sha256(b'edited').digest(), 0), (b'old hash', 1)):
            with self.subTest(digest=digest), patch('mmtr_editor.model.inspect_data', return_value=(None, doc.info)), \
                    patch('mmtr_editor.dxc.Dxc') as decoder, patch('inspect_mmtr.reflection', return_value=reflected):
                result = doc.validate_metadata_interfaces('unused.dll', _in_worker=True,
                    _reflections={0: (digest, reflected)})
                self.assertTrue(result['valid'])
                self.assertEqual(decoder.return_value.disassemble.call_count, expected_calls)
                decoder.return_value.close.assert_called_once()

    def test_validation_reuses_only_same_document_and_dxc_bytes(self):
        doc = document()
        doc.metadata_edits.append({'shader_hashes': {'0': 'proof'}})
        with tempfile.TemporaryDirectory() as folder:
            dll = Path(folder) / 'dxcompiler.dll'
            dll.write_bytes(b'version1')
            report = {'valid': True, 'issues': []}
            with patch('mmtr_editor.material_process.run_material_task', return_value=report) as worker:
                doc.validate_metadata_interfaces(dll)
                cached = doc.validate_metadata_interfaces(dll)
                self.assertEqual(worker.call_count, 1)
                cached['issues'].append('external mutation')
                self.assertEqual(doc.validate_metadata_interfaces(dll)['issues'], [])
                dll.write_bytes(b'version2')
                doc.validate_metadata_interfaces(dll)
                self.assertEqual(worker.call_count, 2)
                doc.metadata_edits[0]['shader_hashes']['0'] = 'changed proof'
                doc.validate_metadata_interfaces(dll)
                self.assertEqual(worker.call_count, 3)
                doc.replacements[0] = b'other shader'
                doc.validate_metadata_interfaces(dll)
                self.assertEqual(worker.call_count, 4)

    def test_repeated_build_reuses_candidate_and_isolates_report(self):
        doc = document()
        doc.metadata_edits.append({'kind': 'test', 'nested': {'value': 1}})
        first = doc.build()
        first[1]['metadata_edits'][0]['nested']['value'] = 99
        second = doc.build()
        self.assertEqual(second[1]['metadata_edits'][0]['nested']['value'], 1)
        self.assertEqual(doc.metadata_edits[0]['nested']['value'], 1)
        self.assertEqual(doc.current_info()['metadata'][0]['nested']['value'], 1)
        self.assertEqual(doc._build_uncached.call_count, 1)

    def test_all_edit_inputs_invalidate_even_when_mutated_in_place(self):
        edits = [lambda d: d.replacements.update({0: b'changed'}),
                 lambda d: d.renames.update({4: 'new'}),
                 lambda d: d.redirects.update({(0, 8): 1}),
                 lambda d: d.metadata_edits[0]['nested'].update(value=2),
                 lambda d: d.replacement_notes.update({0: {'expert': True}}),
                 lambda d: d.integrity['name_hash_mismatches'].append(4),
                 lambda d: setattr(d, 'source', Path('other.mmtr')),
                 lambda d: setattr(d, 'original', b'other original')]
        for edit in edits:
            with self.subTest(edit=edit):
                doc = document()
                doc.metadata_edits.append({'nested': {'value': 1}})
                doc.build()
                edit(doc)
                doc.build()
                self.assertEqual(doc._build_uncached.call_count, 2)

    def test_reset_and_transaction_copy_do_not_return_stale_results(self):
        doc = document()
        doc.build()
        backup = copy.deepcopy(doc)
        doc.replacements[0] = b'new'
        doc.build()
        self.assertEqual(backup.build()[0], b'candidate')
        self.assertEqual(backup._build_uncached.call_count, 1)
        doc.reset()
        self.assertEqual(doc.build()[0], b'original')
        self.assertEqual(doc.current_info(), doc.info)

    def test_failed_build_does_not_poison_previous_cache(self):
        doc = document()
        doc.build()
        doc.replacements[0] = b'invalid'
        doc._build_uncached.side_effect = ValueError('rejected')
        with self.assertRaisesRegex(ValueError, 'rejected'):
            doc.build()
        doc.replacements[0] = b'edited'
        self.assertEqual(doc.build()[0], b'candidate')


if __name__ == '__main__':
    unittest.main()
