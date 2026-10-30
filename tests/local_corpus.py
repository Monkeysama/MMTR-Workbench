"""本地集成测试语料：固定原版身份，不读取随实验安装变化的游戏目录。"""
import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 指向包含 natives 的 MHWILDS 解包目录；若目录结构是 re_chunk_000/natives，则指向 re_chunk_000。
MHWILDS_EXTRACT = Path(os.environ.get(
    'MMTR_TEST_MHWILDS_EXTRACT', 'F:/MH Wilds/Game Unpack/MHWILDS_EXTRACT'))
MHWILDS_VARIATION = MHWILDS_EXTRACT / 'natives/STM/MaterialShader/Variation'

# MDF 绑定/迁移语料根；目录内需有 archives 与 closest_native。
DETAILCOL_CORPUS = Path(os.environ.get(
    'MMTR_TEST_DETAILCOL_CORPUS', str(ROOT / 'output/pix/detailcol_reference')))

ORIGINAL_TOP_MDF_SHA256 = '2cfb7bfea54cebbd3e5f197cddbc2673dac0b0159fbeb4b94747d2d3674a3f46'
ORIGINAL_TOP_MDF = Path(os.environ.get('MMTR_TEST_ORIGINAL_MDF',
    str(ROOT / 'output/pix/top_independent_masters/baseline/original.mdf2.45')))


# 缺少文件由用例显式skip；已有文件身份错误必须失败，不能静默退回游戏或另一份样本。
def verify_original_top_mdf():
    actual = hashlib.sha256(ORIGINAL_TOP_MDF.read_bytes()).hexdigest()
    if actual != ORIGINAL_TOP_MDF_SHA256:
        raise ValueError(f'Original MDF corpus hash mismatch: {ORIGINAL_TOP_MDF}; expected {ORIGINAL_TOP_MDF_SHA256}, got {actual}')
