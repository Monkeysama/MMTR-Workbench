"""开发环境MCP入口；加载本地依赖的.pth，标准输出仅用于stdio协议。"""
from pathlib import Path
import site
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
site.addsitedir(str(ROOT / '.build' / 'editor-deps'))

if __name__ == '__main__':
    try:
        from mmtr_editor.mcp_server import main
    except ImportError as exc:
        print('MCP依赖未就绪：运行 npm run core:build 或 pip install -r requirements-editor.txt。'
              f'\n{exc}', file=sys.stderr)
        sys.exit(1)
    main()
