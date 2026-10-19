# 通过GitHub Actions发布

正式构建在GitHub托管的 `windows-2025` runner执行。推送 `v` 开头的SemVer tag后，Tag release工作流从该tag检出源码、同步版本、测试、构建Windows x64便携包，并上传对应GitHub Release。无需本地编译，也不读取本机已有EXE或游戏资产。

```powershell
git tag v1.0.0
git push origin v1.0.0
```

`v1.0.0` 生成版本 `1.0.0`，包括界面左上角、Python/MCP、Tauri、Cargo与ZIP文件名。版本仅写入runner的临时检出，不向main自动提交改版。`v1.1.0-rc.1` 等预发布tag会生成GitHub prerelease；不符合SemVer的tag在构建前失败。

Release附件为 `MMTR-Workbench-1.0.0-windows-x64.zip` 和 `SHA256SUMS.txt`。ZIP含桌面EXE、独立核心、MCP配置和说明；解压后需要把MCP配置的示例路径改为实际EXE位置。工作流核对冻结MCP版本、20工具及合成编辑事务，再核对包内manifest与ZIP字节。未知游戏布局及真实画面不属于该发布检查。

只需仓库自带的 `GITHUB_TOKEN`，发布job拥有contents:write，构建job只读；不需要额外PAT。GitHub仓库需启用Actions，并允许工作流写入Releases。重跑同tag工作流会更新同名附件，tag仍指向原提交；不要靠移动已发布tag更新源码。

Actions页面也可手动运行 **Tag release**，输入如 `v1.0.0` 进行云端验证。手动运行只生成Actions Artifacts，不创建或更新Release；正式发布仍由推送tag触发。常规分支CI不会重复执行tag构建。
