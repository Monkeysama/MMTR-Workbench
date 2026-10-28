"""固定资源事实验证并发上限，不依赖测试机的核数、负载或内存。"""
import unittest
from unittest.mock import patch

from mmtr_editor.material_scheduling import MIB, select_workers, validate_max_workers
from mmtr_editor.model import EditError


class MaterialSchedulingTests(unittest.TestCase):
    def choose(self, cpu=28, memory=16*1024**3, cap=None, source=26*MIB):
        with patch('mmtr_editor.material_scheduling.available_resources', return_value=(cpu, memory)):
            return select_workers(source, cap)

    def test_cpu_and_manual_limits_never_exceed_half_available_cores_or_eight(self):
        for cpu, expected in ((28, 8), (8, 4), (3, 1), (1, 1)):
            with self.subTest(cpu=cpu):
                self.assertEqual(self.choose(cpu=cpu)['policy_limit'], expected)
        self.assertEqual(self.choose(cap=6)['policy_limit'], 6)
        self.assertEqual(self.choose(cpu=8, cap=8)['policy_limit'], 4)
        self.assertEqual(self.choose(cap=1)['policy_limit'], 1)

    def test_memory_and_document_copy_estimate_reduce_concurrency(self):
        self.assertEqual(self.choose(memory=2*1024**3)['policy_limit'], 4)
        self.assertEqual(self.choose(memory=1024**3)['policy_limit'], 1)
        result = self.choose(memory=2*1024**3, source=128*MIB)
        self.assertEqual(result['estimated_worker_bytes'], 640*MIB)
        self.assertEqual(result['policy_limit'], 1)

    def test_unknown_resources_are_conservative_and_caps_are_strict(self):
        for cpu, memory in ((None, 16*1024**3), (28, None), (None, None)):
            self.assertEqual(self.choose(cpu=cpu, memory=memory)['policy_limit'], 1)
        for value in (0, 9, True, 2.5, '4'):
            with self.subTest(value=value), self.assertRaises(EditError):
                validate_max_workers(value)
        self.assertEqual(self.choose()['mode'], 'auto')
        self.assertEqual(self.choose(cap=4)['requested_max_workers'], 4)
