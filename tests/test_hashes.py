"""独立哈希模块使用公开标准向量，避免迁移时改变文件身份。"""
import random
import runpy
import sys
import unittest
from unittest.mock import patch
from mmtr_editor import hashes
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

    def test_missing_native_dependency_keeps_reference_hashes(self):
        with patch.dict(sys.modules, {'xxhash': None}):
            fallback = runpy.run_path(hashes.__file__)
        self.assertEqual(fallback['XXH64_BACKEND'], 'python')
        for data, expected in ((b'', 0xef46db3751d8e999), (b'a', 0xd24ec4f1a98c6e5b),
                               (b'hello', 0x26c7827d889f6da3)):
            self.assertEqual(fallback['xxh64'](data), expected)
        self.assertEqual(fallback['xxh64'](bytes(range(256)), -1),
                         fallback['xxh64'](bytes(range(256)), (1 << 64) - 1))

    @unittest.skipUnless(hashes.XXH64_BACKEND == 'native', '未安装xxhash；纯Python后备仍验证标准向量')
    def test_native_matches_reference_at_block_tail_and_seed_boundaries(self):
        randomizer = random.Random(20261029)
        for size in list(range(257)) + [1024, 4095, 4096, 4097, 65537]:
            data = randomizer.randbytes(size)
            for seed in (0, 1, 0xffffffff, (1 << 64) - 1, -1, (1 << 64) + 17):
                with self.subTest(size=size, seed=seed):
                    self.assertEqual(xxh64(data, seed), hashes._xxh64_python(data, seed))
                    self.assertEqual(xxh64(memoryview(data), seed), hashes._xxh64_python(data, seed))
