"""多标签 RPC 的文档、编译载荷、任务和源文件保护回归；只使用合成输入。"""
import os
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

from test_structure import fixture as mmtr_fixture
from test_mdf_editing import fixture as mdf_fixture
from mmtr_editor.material_jobs import TERMINAL
from mmtr_editor.model import EditError
from mmtr_editor.rpc import RpcSession


class RpcWorkspaceTests(unittest.TestCase):
    """每个上下文独立所有权；旧请求省略 document_id 时仍使用 default。"""
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        # Windows runner 的 TEMP 可能使用 8.3 短路径，与核心返回的规范路径保持一致。
        self.root = Path(self.folder.name).resolve()
        self.session = RpcSession()
        self.addCleanup(self.session.close)
        for name in ('a', 'b'):
            (self.root / f'{name}.mmtr.240906211').write_bytes(mmtr_fixture())
            (self.root / f'{name}.mdf2.45').write_bytes(mdf_fixture())
        self.call('workspace_new', 'other')

    def call(self, op, document_id='default', **args):
        return self.session.handle(dict(op=op, document_id=document_id, **args))

    def open_pair(self, name, context):
        self.call('open', context, path=str(self.root / f'{name}.mmtr.240906211'))
        self.call('mdf_open', context, path=str(self.root / f'{name}.mdf2.45'), material_index=0)

    def test_documents_selection_compiled_payload_and_revisions_are_isolated(self):
        self.open_pair('a', 'default')
        self.open_pair('b', 'other')
        revision = self.session.contexts['other'].revision
        self.call('mdf_select', material_index=1)
        self.call('mdf_set_property', name='Value', values=[0.75], material_index=1)
        self.session.service.compiled = b'compiled in default'
        self.assertEqual(self.call('mdf_snapshot', 'other')['selected']['index'], 0)
        self.assertEqual(self.call('mdf_snapshot', 'other')['selected']['properties'][0]['values'], [0.5])
        self.assertEqual(self.session.contexts['other'].revision, revision)
        self.assertIsNone(self.session.contexts['other'].service.compiled)
        self.assertEqual(self.session.handle({'op': 'snapshot'})['source'], str(self.root / 'a.mmtr.240906211'))
        listing = self.call('workspace_list')['documents']
        self.assertEqual([d['dirty'] for d in listing], [True, False])
        self.assertEqual(listing[0]['mdf_source'], str(self.root / 'a.mdf2.45'))

    def test_duplicate_sources_and_hardlinks_are_rejected_without_replacing_document(self):
        self.open_pair('a', 'default')
        self.open_pair('b', 'other')
        before = self.call('snapshot', 'other')
        with self.assertRaisesRegex(EditError, '其他标签'):
            self.call('open', 'other', path=str(self.root / 'a.mmtr.240906211'))
        link = self.root / 'linked.mdf2.45'
        os.link(self.root / 'a.mdf2.45', link)
        with self.assertRaisesRegex(EditError, '其他标签'):
            self.call('mdf_open', 'other', path=str(link))
        self.assertEqual(self.call('snapshot', 'other'), before)
        self.assertEqual(self.call('mdf_snapshot', 'other')['source'], str(self.root / 'b.mdf2.45'))

    def test_bad_parse_preserves_both_contexts(self):
        self.open_pair('a', 'default')
        self.open_pair('b', 'other')
        before = [self.call('snapshot', context) for context in ('default', 'other')]
        bad = self.root / 'bad.mmtr.240906211'
        bad.write_bytes(b'not an mmtr')
        with self.assertRaises(Exception):
            self.call('open', 'other', path=str(bad))
        after = [self.call('snapshot', context) for context in ('default', 'other')]
        for snapshot, original in zip(after, before):
            snapshot.pop('revision')
            original.pop('revision')
        self.assertEqual(after, before)

    def test_find_locates_mmtr_mdf_and_hardlinks_without_changing_selection(self):
        self.open_pair('a', 'default')
        self.open_pair('b', 'other')
        link = self.root / 'linked.mdf2.45'
        os.link(self.root / 'b.mdf2.45', link)
        for path, owner in ((self.root / 'a.mmtr.240906211', 'default'), (link, 'other'),
                            (self.root / 'missing.mmtr.240906211', None)):
            self.assertEqual(self.call('workspace_find', path=str(path))['document_id'], owner)
        self.assertEqual(self.call('mdf_snapshot', 'other')['selected']['index'], 0)

    def test_every_export_protects_other_tabs_sources_and_hardlinks(self):
        self.open_pair('a', 'default')
        self.open_pair('b', 'other')
        link = self.root / 'linked.mmtr.240906211'
        os.link(self.root / 'b.mmtr.240906211', link)
        for op in ('export', 'export_shader', 'report', 'export_layout', 'export_compiled', 'mdf_export', 'mdf_report', 'material_project_save'):
            for target in (link, self.root / 'b.mdf2.45'):
                with self.subTest(op=op, target=target), self.assertRaisesRegex(EditError, '任一已打开'):
                    self.call(op, path=str(target), index=0)
        self.assertEqual(link.read_bytes(), mmtr_fixture())
        self.assertEqual((self.root / 'b.mdf2.45').read_bytes(), mdf_fixture())

    def test_close_requires_discard_and_retired_ids_cannot_receive_late_requests(self):
        self.open_pair('a', 'default')
        self.call('mdf_set_property', name='Value', values=[0.75], material_index=0)
        with self.assertRaisesRegex(EditError, '确认放弃'):
            self.call('workspace_close')
        self.call('workspace_close', discard=True)
        for op in ('snapshot', 'workspace_new', 'mdf_set_property'):
            with self.subTest(op=op), self.assertRaises(EditError):
                self.call(op)
        self.assertIsNone(self.call('snapshot', 'other'))

    def test_mdf_export_clears_unsaved_without_changing_source(self):
        self.open_pair('a', 'default')
        self.call('mdf_set_property', name='Value', values=[0.75], material_index=0)
        self.call('mdf_export', path=str(self.root / 'saved.mdf2.45'))
        self.assertFalse(self.call('workspace_list')['documents'][0]['dirty'])
        self.assertEqual((self.root / 'a.mdf2.45').read_bytes(), mdf_fixture())

    def test_background_job_stays_in_owner_while_other_tab_changes(self):
        self.open_pair('a', 'default')
        self.open_pair('b', 'other')
        dll = self.root / 'identity.dll'
        dll.write_bytes(b'identity only, never loaded')
        # 通过真实打开恢复与合成 MDF 匹配的源路径，保持文档缓存及身份一致。
        source = self.root / 'MaterialShader' / 'test.mmtr.240906211'
        source.parent.mkdir()
        source.write_bytes(mmtr_fixture())
        self.call('open', path=str(source))
        self.call('mdf_open', path=str(self.root / 'a.mdf2.45'))
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        actions = [dict(action='parameter', name='Extra', byte_size=4)]
        plan = self.call('material_job_preview', actions=actions, record_index=0, dll=str(dll))

        def apply(candidate, *args, worker_control, **kwargs):
            entered.set()
            release.wait(3)
            worker_control.check()
            candidate.mdf.set_property(0, 'Value', [0.75])
            return {'plan': plan, 'interface_sync': {'shader_reports': []}}

        with patch('mmtr_editor.material_jobs.apply_material_batch', side_effect=apply):
            job = self.call('material_job_submit', actions=actions, record_index=0, dll=str(dll), expected_plan=plan['digest'], request_key='same')
            self.assertTrue(entered.wait(2))
            restored = self.call('workspace_state')
            self.assertEqual([row['job_id'] for row in restored['jobs']], [job['job_id']])
            self.assertEqual(restored['doc']['source'], str(self.session.service.doc.source))
            self.assertEqual(restored['mdf']['selected']['properties'][0]['values'], [0.5])
            self.assertEqual(self.call('workspace_state', 'other')['jobs'], [])
            with self.assertRaises(EditError):
                self.call('material_job_status', 'other', job_id=job['job_id'])
            with self.assertRaisesRegex(EditError, '确认放弃'):
                self.call('workspace_close')
            self.call('mdf_set_property', 'other', name='Value', values=[0.25], material_index=0)
            other_revision = self.session.contexts['other'].revision
            release.set()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                status = self.call('material_job_status', job_id=job['job_id'])
                if status['state'] in TERMINAL:
                    break
                time.sleep(0.01)
            self.assertEqual(status['state'], 'succeeded', status)
            self.assertEqual(self.call('workspace_state')['jobs'], [])
            self.assertEqual(self.call('workspace_state')['mdf']['selected']['properties'][0]['values'], [0.75])
            self.assertEqual(self.call('mdf_snapshot')['selected']['properties'][0]['values'], [0.75])
            self.assertEqual(self.call('mdf_snapshot', 'other')['selected']['properties'][0]['values'], [0.25])
            self.assertEqual(self.session.contexts['other'].revision, other_revision)


if __name__ == '__main__':
    unittest.main()
