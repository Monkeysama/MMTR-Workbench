# 贡献规范

先阅读 [README](README.md)、[AGENTS.md](AGENTS.md) 与[开发说明](docs/development.md)。每次改动围绕一个明确问题，说明期望行为和验收方法。代码与整理文档提交本仓库；游戏资产、捕获、生成 IR 和个人路径配置仅保留本地。

## 提交格式

遵循 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)：

```text
<type>[optional scope][!]: <description>

[optional body]

[optional footer]
```

使用 ASCII 冒号 `:`，其后一个空格；描述可用中文、英文或其他语言。`feat：改了xx` 中的全角冒号不符合本项目格式，请写成 `feat: 改了xx`。

|类型|适用改动|
|---|---|
|`feat`|新增能力|
|`fix`|修复问题|
|`docs`|文档|
|`refactor`|不改变行为的代码整理|
|`test`|测试|
|`build`|依赖、编译或打包|
|`ci`|持续集成|
|`chore`|其他维护|
|`perf` / `style` / `revert`|性能、纯格式或回退|

```text
feat(mdf): 支持从关联 MMTR 选择参数
fix(mcp): restore document after a failed batch
build: 更新桌面打包依赖
feat(rpc)!: 统一请求字段命名
```

破坏性改动在正文或 `BREAKING CHANGE: ...` 页脚写明影响和迁移步骤。版本升级与发布应区分新增、修复和破坏性改动，不仅修改显示版本。

```powershell
npm run setup:hooks
```

这会为当前检出设置 `.githooks`，提交时检查格式；不会修改全局 Git 设置。CI 同时检查 PR 标题或新推送的提交。

## 提交前

运行相关测试和构建；检查暂存差异、空白和资产审计。保持变更范围明确，PR 写清问题、最终行为、验证命令与局限。真实语料缺失导致的 skip 与通过分开报告。无需为了满足测试前提修改用户游戏目录。

新任务属于这个仓库时不修改 mmtr-research 的议题或主线计划。用户未要求时不自动合并 PR、发布版本或安装游戏文件。
