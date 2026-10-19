"""初始DXC配置由用户环境提供，不依赖某台开发机的固定路径。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from mmtr_editor import __version__
from mmtr_editor.rpc import Service


class RuntimeConfigTests(unittest.TestCase):
    def test_absent_or_missing_dxc_is_empty(self):
        for value in ('', 'missing/dxcompiler.dll'):
            with self.subTest(value=value), patch.dict(os.environ, {'MMTR_DXC_PATH': value}):
                self.assertEqual(Service().handle({'op': 'ping'})['dxc'], '')

    def test_existing_path_is_resolved_without_loading_native_library(self):
        with tempfile.TemporaryDirectory() as folder:
            dll = Path(folder) / 'dxcompiler.dll'
            dll.write_bytes(b'fixture-only')
            with patch.dict(os.environ, {'MMTR_DXC_PATH': str(dll)}):
                result = Service().handle({'op': 'ping'})
            self.assertEqual(result['dxc'], str(dll.resolve()))
            self.assertEqual(result['version'], __version__)
