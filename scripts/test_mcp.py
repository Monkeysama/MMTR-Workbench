"""加载本地SDK后运行MCP契约测试；不依赖游戏资产或修改客户端设置。"""
from pathlib import Path
import site
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
site.addsitedir(str(ROOT / '.build' / 'editor-deps'))

if __name__ == '__main__':
    from mmtr_editor.mcp_server import create_server  # 缺SDK时明确失败，不以全部skip当作验收。
    unittest.main(module='test_mcp', verbosity=2)
