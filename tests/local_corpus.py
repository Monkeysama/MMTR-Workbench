"""本地集成测试语料：固定原版身份，不读取随实验安装变化的游戏目录。"""
import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_TOP_MDF_SHA256 = '2cfb7bfea54cebbd3e5f197cddbc2673dac0b0159fbeb4b94747d2d3674a3f46'
ORIGINAL_TOP_MDF = Path(os.environ.get('MMTR_TEST_ORIGINAL_MDF',
    str(ROOT / 'output/pix/top_independent_masters/baseline/original.mdf2.45')))


# 缺少文件由用例显式skip；已有文件身份错误必须失败，不能静默退回游戏或另一份样本。
def verify_original_top_mdf():
    actual = hashlib.sha256(ORIGINAL_TOP_MDF.read_bytes()).hexdigest()
    if actual != ORIGINAL_TOP_MDF_SHA256:
        raise ValueError(f'Original MDF corpus hash mismatch: {ORIGINAL_TOP_MDF}; expected {ORIGINAL_TOP_MDF_SHA256}, got {actual}')
