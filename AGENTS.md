# MMTR Workbench 项目规范

## 范围与协作

- 本仓库专注 MMTR/MDF 桌面编辑器、Python 编辑核心和 MCP。DLL 注入、GPU 回放及游戏渲染路线研究属于 mmtr-research，不迁入这里作为当前主线。
- 用户明确要求优先。迁移采用复制，不删除或修改原研究仓库文件；不得更新原仓库议题、PR 或交付计划来推进本仓库任务。
- 开始前读取本文件、README、目标代码及相关文档；有对应议题时核对范围和验收，没有议题的明确用户任务可直接按该任务推进。只在用户要求时创建或修改远端议题。
- 默认一次推进一个实现任务。先说明目标、最小改动及验证方法；新发现的非阻塞问题记到后续，不扩大为模板扫描、通用框架或另一条实现路线。
- 不主动创建新聊天或子代理。保留用户修改和本地证据，不为获得干净状态覆盖或清理它们。

## 架构与代码

- Vue 使用 Composition API、`<script setup lang="ts">` 和显式 props/emits。界面组件负责展示，文档状态及请求由 composables 管理；纯转换放入 utils/lib。
- Python 模型与验证放在 mmtr_editor；桌面 JSON-lines RPC 与 MCP 复用相同编辑核心。运行时代码不能导入 mmtr-research 或依赖另一个检出目录。
- 主要函数、类及模块使用中文注释说明职责、线程、资源所有权及关键约束；第三方代码保留上游格式和许可。
- 所有文档编辑以候选事务进行，失败保留原状态；未知格式、接口或寻址必须明确拒绝，不猜测字段、不静默丢弃未知字节。
- 源文件保护、重解析、名称/载荷哈希、关联材质校验及失败回滚必须保留。不能仅凭反射验证宣称 Shader 地址迁移或游戏效果正确。
- 原生 DXC 工作在隔离子进程中执行；MCP 使用独立会话和串行锁，stdout 只输出协议，不混入日志。文件导出前预检路径，保护打开的源文件和硬链接。

## 验证与交付

- 助手负责实现、构建、自检和分析日志；需要游戏内验证时只提供必要步骤、预期结果及反馈点。
- 改动先跑相关测试，再按影响检查全套无语料测试和构建；迁移/依赖改动必须证明独立目录可导入和构建，不能借用旧仓库的 Python 模块或预编译核心冒充成功。
- 常用检查：`npm run test:core`、`npm run test:mcp`、`npm run test:frontend`、`npm run build`；桌面交付另跑 `npm run desktop:build` 和冻结 MCP 验收。
- 缺 DXC/真实语料的 skip 必须报告，不等于通过。分别报告静态/CPU、桌面或冻结进程、GPU 和真实游戏证据；本仓库不自动部署游戏资产。
- 版本变化同步 package.json/package-lock.json、Cargo.toml/Cargo.lock、tauri.conf.json 和 mmtr_editor/__init__.py；左上角显示直接使用 package.json 版本。
- 研究/验收日志和生成文件保存在忽略的 output/；docs 只保留整理后的结论，写明输入、条件、结果、局限和下一步。

## Git 与提交

- 提交使用 Conventional Commits：`type(scope): description`，scope 可省略，破坏性改动用 `!` 并在正文说明。描述语言随意，必须使用 ASCII 冒号加空格。
- 常见类型：feat、fix、docs、refactor、test、build、ci、chore、perf、style、revert。示例：`feat(mcp): 新增材质引用编辑`、`fix(mdf): preserve shared strings`。
- 只暂存明确文件清单。提交前审查 `git diff --cached`，执行 `git diff --cached --check` 和 `python scripts/audit_repository.py --staged`。
- 游戏资产、完整导出 IR、捕获、DLL/EXE、构建目录、依赖缓存、凭据及个人配置不入库；应用图标是明确保留的资源例外。
- 可用 `npm run setup:hooks` 启用本仓库 commit-msg 校验。PR 说明问题、最终行为、验证和剩余限制；没有议题时不编造编号，不使用 Closes 关闭未验收工作。
- 推送后核对远端 SHA 与 CI；合并、发布和游戏安装按用户授权执行，不由推送成功自动触发。
