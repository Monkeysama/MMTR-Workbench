# 开发与验证

## 环境与启动

Python 3.11+，Node.js 22.18+，npm；Windows 桌面另需 Rust stable、MSVC C++ Build Tools、Windows SDK、WebView2。Python 基础解析无需第三方库，MCP 使用 requirements-editor.txt 中锁定的官方 SDK。

```powershell
npm ci
python -m pip install -r requirements-editor.txt
npm run dev
```

开发网页通过 Vite 的本机桥连接 Python RPC，仅监听127.0.0.1:5173。桌面开发先执行 `npm run core:build`，再 `npm run desktop:dev`；已有服务占用端口时先结束对应旧开发实例。不要同时运行两份占同端口的服务。

`npm run core:build` 在本项目 `.build/` 准备 PyInstaller 和 MCP SDK，再冻结核心。更新依赖前结束使用同一依赖目录的开发MCP，避免Windows文件锁。Node/Rust依赖由锁文件管理；不拷贝旧项目node_modules或构建产物作为新构建结果。

DXC 为用户自行提供的 Microsoft dxcompiler.dll。工具设置可以指定路径；Python核心也支持 `MMTR_DXC_PATH` 作为初始路径。未配置时不绑定某位开发者的安装目录。DLL不进入Git。

## 检查

```powershell
npm run test:core
npm run test:mcp
npm run test:frontend
npm run build
git diff --cached --check
python scripts/audit_repository.py --staged
```

核心测试由项目脚本加载本地构建SDK（若存在）或已安装依赖。MCP测试会在SDK缺失时明确失败，避免整组skip被误认为通过。合成数据测试不需要游戏；真实样本、旧候选、完整IR及DXC用例缺输入时明确skip，不在CI下载游戏资产。`output/`路径是可选本地语料约定，不要求存在旧研究检出目录。

大部分历史集成用例的语料默认路径仍继承旧样本布局，可在独立仓库准备自有只读语料。真实Top MDF通过 `MMTR_TEST_ORIGINAL_MDF` 指定，必须匹配用例锁定hash，不读取正在被MOD修改的游戏MDF。源码迁移不复制原output；可选语料测试不等于独立合成测试。

## 构建与打包

```powershell
npm run desktop:build
python scripts/package_editor.py
```

或运行 `npm run package`。输出在release；不要提交EXE/DLL/ZIP、.build、src-tauri/target、src-tauri/binaries或生成schema。可用 `python scripts/package_editor.py --output-dir release/MMTR-Workbench-0.3.0` 生成独立便携目录，避免覆盖运行中的EXE。

打包后核对manifest、冻结核心版本、GUI版本及真实MCP初始化。`python scripts/smoke_workbench_standalone.py` 使用合成MDF验证源码stdio；加 `--executable release/MMTR-Workbench/mmtr-core.exe` 验证冻结服务，两者都在临时工作目录运行，不要求任何游戏文件。通用stdio验收脚本为 `scripts/smoke_workbench_mcp.py`，输入参数见 `--help`；该完整场景需要自有兼容MMTR/MDF和DXC，不自动安装到游戏。MCP配置移动目录后需更新command绝对路径。

## 证据边界

单元测试、类型检查、DXC验证、桌面构建及游戏效果是不同层级。记录输入身份、命令、退出码、跳过项与限制。首次独立迁移记录见[migration.md](migration.md)，无需复用研究仓库的DLL议题或GPU流水。
