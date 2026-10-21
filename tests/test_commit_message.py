"""提交格式在中文/英文标题、scope及破坏性标记下保持一致。"""
import unittest
from unittest.mock import patch
from scripts.check_commit_message import event_headers, valid_header


class CommitMessageTests(unittest.TestCase):
    def test_supported_headers(self):
        for header in ('feat: 新增参数', 'fix(mcp): preserve rollback', 'feat(rpc)!: 更改协议', 'docs: 更新文档'):
            with self.subTest(header=header):
                self.assertTrue(valid_header(header))

    def test_invalid_headers(self):
        for header in ('feat：改了xx', 'feat:no space', 'feat: ', 'changed things', 'feat:  extra space', 'feat(): empty scope'):
            with self.subTest(header=header):
                self.assertFalse(valid_header(header))

    # 新分支推送的 before 为全零：只取默认分支之外的提交；默认分支引用缺失时退回 after。
    def test_new_branch_push_checks_only_commits_outside_default_branch(self):
        event = {'before': '0' * 40, 'after': 'abc', 'repository': {'default_branch': 'main'}}
        with patch('scripts.check_commit_message.subprocess.run') as run, \
                patch('scripts.check_commit_message.subprocess.check_output', return_value='feat: x\n') as log:
            run.return_value.returncode = 0
            self.assertEqual(event_headers(event), ['feat: x'])
            self.assertEqual(log.call_args[0][0][-1], 'origin/main..abc')
            run.return_value.returncode = 1
            event_headers(event)
            self.assertEqual(log.call_args[0][0][-1], 'abc')
        with patch('scripts.check_commit_message.subprocess.check_output', return_value='') as log:
            event_headers({'before': 'def', 'after': 'abc'})
            self.assertEqual(log.call_args[0][0][-1], 'def..abc')


if __name__ == '__main__':
    unittest.main()
