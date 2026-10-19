"""加载本项目可选SDK后运行全部编辑器测试；不借用研究仓库。"""
from pathlib import Path
import site
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
site.addsitedir(str(ROOT / '.build' / 'editor-deps'))

# 合成测试始终执行；真实语料/DXC用例由各自skip条件报告。
def main():
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1

if __name__ == '__main__':
    raise SystemExit(main())
