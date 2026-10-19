"""独立哈希模块使用公开标准向量，避免迁移时改变文件身份。"""
import unittest
from mmtr_editor.hashes import murmur3_x86_32, xxh64


class HashTests(unittest.TestCase):
    def test_murmur3_known_vectors(self):
        self.assertEqual(murmur3_x86_32(b'', 0), 0)
        self.assertEqual(murmur3_x86_32(b'hello', 0), 0x248bfa47)
        self.assertEqual(murmur3_x86_32(b'foo', 0), 0xf6a5c420)

    def test_xxhash64_known_vectors(self):
        self.assertEqual(xxh64(b''), 0xef46db3751d8e999)
        self.assertEqual(xxh64(b'a'), 0xd24ec4f1a98c6e5b)
        self.assertEqual(xxh64(b'hello'), 0x26c7827d889f6da3)
