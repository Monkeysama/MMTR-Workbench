# MMTR Workbench

用于 RE Engine MMTR 着色器与 MDF 材质文件的桌面编辑器。支持怪物猎人荒野与 RE4（BIOHAZARD RE:4），RE4 的差异与验证范围见 [RE4 支持](docs/re4-support.md)。

## 功能

- 浏览完整同名序列 MMTR（荒野四组或五组，RE4 三组）的渲染通道、Shader、常量缓冲和资源，支持带数字版本后缀的文件。
- 查看 DXIL 反汇编和可读伪代码，在已支持范围内重建 HLSL、编译与替换 Shader。
- 编辑 MDF2.45（荒野）与 MDF2.32（RE4）的材质参数、纹理路径和设置，增删参数/贴图槽，按 MMTR 路径筛选关联材质。
- 新增 UserMaterial 尾部参数或 Texture2D 接口，迁移已识别的材质寻址、布局和关联资源表，并验证后另存。
- 通过 **34 个 MCP 工具**执行查询、后台混合批量追加、独立变体、材质工程、读取统计、有界删除、效果模板预检和受保护导出；每个 MCP 进程独立持有文档。

自动接口扩展需要关联 MDF 与 DXC，支持 4/8/12/16 字节 UserMaterial 尾部参数、已有 PS 表的固定 float4 Texture2D，以及能够证明 MDF 槽身份的原生 `bindlessTexture2D`／资源堆 Texture2D。原生路径保留已有动态绑定，迁移原字段地址和已验证的布局标记；新增槽不会自动生成采样效果。未知布局、Sampler/UAV 和无法证明的混合寻址会拒绝操作。

删除仅支持经过完整消费者证明的无作用接口，会同步已知关联 MDF，并在隔离 DXC 中重编译和重解析候选；验证通过不代表任意游戏版本或材质的渲染效果已通过。`emission_multiplier` 效果模板只提供显式 Shader 合同，必须先编译匹配 Shader。

## 使用

下载后解压运行 `MMTR Workbench.exe`，保留同目录的 `mmtr-core.exe`。在工具设置中配置自己的 Microsoft `dxcompiler.dll`；DLL 和游戏资产不随仓库提供。打开、浏览与原样另存不需要 DXC。

工具保护当前打开的源文件，编辑结果必须另存。工具本身不自动安装 MOD。详细操作见[使用说明](工具使用说明.md)，智能体连接见[MCP 使用说明](docs/workbench-mcp.md)。

## 开发

建议 Windows 10/11 x64、Python 3.11+、Node.js 22.18+、npm、Rust stable，以及 Tauri 2 所需的 MSVC C++ Build Tools 和 WebView2。

```powershell
npm ci
python -m pip install -r requirements-editor.txt
npm run core:build
npm run desktop:dev
```

`core:build` 将 PyInstaller 与 MCP 构建依赖安装到项目 `.build/`，生成桌面所需 sidecar。仅调试网页与 Python 核心时运行 `npm run dev`，服务监听 `127.0.0.1:5173`；开发端口已被占用时先关闭旧开发实例。

```powershell
npm run test:core
npm run test:mcp
npm run test:frontend
npm run build
npm run package
```

`npm run package` 执行桌面构建并生成 `release/MMTR-Workbench/` 和版本 ZIP；MCP 配置随包提供。合成测试无需游戏资产，本地语料或 DXC 缺失时相关测试会明确跳过。跳过不等于相关能力通过。

## 项目结构

|目录|职责|
|---|---|
|`src/`|Vue 界面、文档状态和前端 RPC 客户端|
|`src-tauri/`|Rust 桌面宿主及 sidecar 管理|
|`mmtr_editor/`|MMTR/MDF 模型、Shader 工具、事务与 MCP 服务|
|`inspect_mmtr.py`|只读 MMTR/DXBC 结构和反射解析|
|`scripts/`|构建、打包、审计与编辑器验收入口|
|`tests/`|合成逻辑、前端、MCP 和可选本地语料测试|
|`docs/`|开发、架构、使用与迁移说明|

提交遵循 **Conventional Commits**，格式为 `type(scope): 描述`，描述可用中文或英文，例如 `feat(mdf): 支持新增贴图槽`、`fix(mcp): 修复批次失败回滚`。冒号使用 ASCII `:`。详见[贡献规范](CONTRIBUTING.md)和[智能体项目规范](AGENTS.md)。

## 文档与许可

- [开发与验证](docs/development.md)
- [架构与边界](docs/architecture.md)
- [MIT License](LICENSE)
