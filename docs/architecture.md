# 架构与边界

Vue界面通过 `src/api.ts` 调用统一请求。网页开发由Vite创建Python JSON-lines桥；桌面由Tauri持有Python子进程并串行交换请求，桌面退出释放子进程。`useWorkbench`管理文档和异步状态，各面板通过props/emits交互。

Python的 `rpc.Service` 持有MMTR/MDF文档和编译缓存；`Document` / `MdfDocument`保留原字节，编辑候选在验证与重解析后另存。`inspect_mmtr.py`解析结构，`mmtr_editor/hashes.py`负责格式校验，不依赖外部研究脚本。

DXC原生调用通过隔离工作进程执行。材质接口扩展迁移已识别的UserMaterial尾部、纹理/GPBF地址和实例步长，同时更新资源名称/绑定双表及计数；未知形式拒绝事务，不能只改metadata。另存保护源文件及硬链接。

MCP入口复用Service和模型，一个进程一个独立会话，RLock串行访问。新增参数、Texture2D及批次动作失败回滚整个候选。桌面已打开文件的内存不与MCP共享。MCP通过stdio传协议，不提供任意代码执行或游戏安装接口。

当前自动迁移仅支持已知布局，删除 Shader 接口和新阶段表仍需显式处理与验证。MMTR 已识别 uniform 与 0x110 per-target 两种文件头；per-target 版本按 REasy profile 登记，鬼武者另有 16 字节材质头。鬼武者的真实语料和游戏内结果来自 PR 作者，本机只有合成测试；其余 per-target 版本仍缺真实语料，mesh/amplification 语义未验证。MDF 已登记 2.10/2.13（RE2/RE3）、2.19/2.21/2.23（RE8/RE2RT/MHRISE）、2.31（SF6）、2.32（RE4）、2.45（荒野）与 2.51（鬼武者）；2.51 目前统一使用鬼武者的 104 字节材质项布局，RE9/Pragmata/DD2 当前版未单独验证。MMTR 已支持样本不等于同引擎全部游戏版本兼容，未知布局必须拒绝。
