"""提交格式在中文/英文标题、scope及破坏性标记下保持一致。"""
import unittest
from scripts.check_commit_message import valid_header


class CommitMessageTests(unittest.TestCase):
    def test_supported_headers(self):
        for header in ('feat: 新增参数', 'fix(mcp): preserve rollback', 'feat(rpc)!: 更改协议', 'docs: 更新文档'):
            with self.subTest(header=header):
                self.assertTrue(valid_header(header))

    def test_invalid_headers(self):
        for header in ('feat：改了xx', 'feat:no space', 'feat: ', 'changed things', 'feat:  extra space', 'feat(): empty scope'):
            with self.subTest(header=header):
                self.assertFalse(valid_header(header))


if __name__ == '__main__':
    unittest.main()
