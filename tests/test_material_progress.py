"""真实文件句柄验证进度替换；长时间持有读句柄也不能让计算失败。"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mmtr_editor.material_control import progress_writer
from mmtr_editor.material_parallel import _progress_value


class MaterialProgressTests(unittest.TestCase):
    def test_open_reader_allows_replacement_and_keeps_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'progress.json'
            writer = progress_writer(path)
            writer('starting', 0, 0)
            with path.open('rb') as reader:
                # 每次换 stage 绕过节流，确保真的执行替换而非碰巧跳过写入。
                for index in range(100):
                    writer(f'phase-{index}', index, 100)
                    value = json.loads(path.read_bytes())
                    expected = (dict(stage='starting', completed=0, total=0) if os.name == 'nt'
                                else dict(stage=f'phase-{index}', completed=index, total=100))
                    self.assertEqual(value, expected)
                self.assertEqual(json.loads(reader.read()), dict(stage='starting', completed=0, total=0))
            writer('shaders', 100, 100)
            self.assertEqual(_progress_value(path, 100), 100)
            self.assertFalse(path.with_suffix('.pending').exists())

    def test_busy_replace_is_retried_on_next_callback_without_throttle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'progress.json'
            writer = progress_writer(path)
            with patch('mmtr_editor.material_control.os.replace', side_effect=PermissionError('busy')):
                writer('shaders', 1, 100)
            writer('shaders', 2, 100)
            self.assertEqual(_progress_value(path, 100), 2)
            # 磁盘等其它错误不能冒充读写竞争而被静默吞掉。
            with patch('mmtr_editor.material_control.os.replace', side_effect=OSError('disk error')):
                with self.assertRaisesRegex(OSError, 'disk error'):
                    writer('other', 3, 100)

    def test_missing_or_invalid_progress_does_not_claim_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'progress.json'
            self.assertEqual(_progress_value(path, 1), 0)
            path.write_bytes(b'{incomplete')
            self.assertEqual(_progress_value(path, 1), 0)


if __name__ == '__main__':
    unittest.main()
