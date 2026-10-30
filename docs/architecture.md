# 架构

Vue界面通过 `src/api.ts` 调用统一请求。网页开发由Vite创建Python JSON-lines桥；桌面由Tauri持有Python子进程并串行交换请求，桌面退出释放子进程。`useWorkbench`管理文档和异步状态，各面板通过props/emits交互。

Python的 `rpc.Service` 持有MMTR/MDF文档和编译缓存；`Document` / `MdfDocument`保留原字节，编辑候选在验证与重解析后另存。`inspect_mmtr.py`解析结构，`mmtr_editor/hashes.py`负责格式校验，不依赖外部研究脚本。

DXC原生调用通过隔离工作进程执行。材质接口扩展迁移已识别的UserMaterial尾部、纹理/GPBF地址和实例步长，同时更新资源名称/绑定双表及计数；未知形式拒绝事务，不能只改metadata。另存保护源文件及硬链接。

MCP入口复用Service和模型，一个进程一个独立会话，RLock串行访问。新增参数、Texture2D及批次动作失败回滚整个候选。桌面已打开文件的内存不与MCP共享。MCP通过stdio传协议，不提供任意代码执行或游戏安装接口。

MMTR 按版本解析 uniform 与 per-target 文件头，并按游戏选择材质数据布局；MDF 按版本解析材质项、属性和纹理表。游戏与版本列表见[多游戏支持](game-support.md)。
