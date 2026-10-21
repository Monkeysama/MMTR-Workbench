# 只读 MMTR 结构检查（P1）

本功能属于用户授权的 REasy 优化计划 P1，版本保持 0.3.0。实现位置为 `mmtr_editor/structure.py`，与原 `inspect_data()`、`Document` 写回器并存。它用于查询结构和共享关系，为批量布局计划提供依据。

## 实验范围

假设：显式对象范围、指针来源及双向索引能发现局部修改可能遗漏的共享者。

最小实验：固定 P0 的四个资源版本 240906211 样本，读取五类资源绑定、状态及输入布局，逐项交叉检查原字节和旧解析结果；合成输入覆盖越界、mask/计数异常、未知类型、共享切片和候选缓存失效。

通过判据：Program/Shader 数量和引用与现有解析一致；所有原字节、未知位保留；所有 Shader/对象使用者可分页取回；结构查询不改变候选，原样重建逐字节一致；不确定解释不影响旧浏览；预检不得把未执行的 DXIL 分析报告为通过。

停止点：只读结构、共享索引和预检报告。接口迁移仍由已有核心负责；本阶段不更换 writer、不执行游戏安装、不开放状态或第五类资源写入。

## 数据模型及身份

快照使用不可变 dataclass/bytes/tuple，保存完整源字节、header、Program、实际 Shader 引用、五类绑定/metadata 引用、常量缓冲成员、输入布局及状态对象。字段来源保存为文件绝对偏移。

- Program 保存完整 0x110 原字节、所属 header 组、原始 compute group/topology 及保留字节。组号不代表已证明的运行时通道选择。
- 绑定条目保存前六个阶段 slot、stage mask、类型代码、metadata 位置及名称。mask 的高位、mask 与存储计数不符、全阶段总数不符均有明确诊断。
- 共享对象按类别、物理起点和尺寸识别。同名不同位置不合并；同址异长或异类，以及部分范围重叠会报告，保留各自身份和使用者。
- Program → Shader/表/状态 与 Shader/表/状态 → 所有 Program 均可查询。共享成员表单独索引，保留其所属 cbuffer 指针位置。
- header 组与解析记录不符、未知资源版本、越界及新解释缺乏证据分别诊断。当前资源版本来自文件名，格式内没有据此被强行解释的版本字段。
- 不对状态枚举猜测名称。光栅/混合/深度包含原始 packed 字段；输入布局保留语义编号、格式编号及元素原值。
- 状态/输入布局后的 8B cache key 为 `opaque`。它们不等于原始状态字节的 Shader xxHash，不能通过重新算 Shader hash 宣称状态已安全可写。
- 尚未解释的 metadata 前缀区段以范围清单保留，包含名称字符串、padding 等。完整 bytes 快照保证这些字段不会被查询丢弃。

扩展结构只在调用新查询时解析；已知四类 metadata 和 Shader 的兼容数据仍来自现有 `inspect_data()`，格式新解释不会反向改变其返回格式。缓存以当前完整候选 SHA256 为键，包括名称、重定向、Shader 和 metadata 编辑后的实际 build 字节，防止使用旧地址。

## 分层报告及调用

RPC：`{"op":"mmtr_inspect_structure","view":"summary","offset":0,"limit":40}`。view 可选 `summary/program/objects/object_users/shader_users/metadata/diagnostics/unmodeled_ranges`，需要指定对象时传 `index`。

MCP 复用 `workbench_inspect`，没有增加新的顶层工具；用法见 [MCP 使用说明](workbench-mcp.md#只读结构预检)。桌面可通过同一 RPC 查询，本阶段未新增可视化面板。所有分页最多 100 项，物理对象原始字节最多预览 256B，完整对象仍保留在不可变模型内。

快速预检只报告新结构解释的结果。完整寻址分析、DXC 编译和接口验证均未在查询中执行，所以 `shader_analysis=not_run`、`auto_extension=unproven`、`state_editing=unsupported`。结构通过也不能替代真实游戏效果验证。

## 参考来源及限制

格式定义参考 REasy 提交 `0eb38af13dc5f2c36826123c36e3693fa03edf12` 的 `file_handlers/sdf/profiles/re_engine_profiles.py`、`sdf_file.py`、`runtime_cache.py`，独立实现，没有引入其 UI、依赖或运行时代码。

荒野固定样本确认 hull legacy lane 可以指向实际 mesh Shader，二者身份同时保留。SRV/UAV 的绑定表可共享完全相同的物理区段，所以跨类别重叠不能直接视为损坏；报告为警告，后续写入必须处理所有受影响使用者。

第五类资源表在四个固定样本中均为空。其非空表只保留候选的 descriptor/metadata 范围并报告 `optional_resource_unverified`，不宣称语义已验证。其它资源版本可继续使用旧浏览，但结构报告明确为未验证版本。

P1 的静态/CPU、真实 stdio 与测试结果记录于本地 `output/reasy-p1-20261021/`；资产和完整报告不入库。GPU、捕获回放、真实游戏均未执行。

## 2026-10-21 验证节点

固定源输入身份沿用 P0 manifest，先核对 SHA256，再读取以下样本。除这四个源快照，还逐个重开 P0 Stocking 的三次原样候选和十二个扩展候选，共 19 个快照；Program/Shader 引用、metadata、反向使用者与旧解析结果逐项一致，所有新模型对象的 raw 与其文件范围一致，查询前后 MMTR 原样重建均逐字节相同。

|源样本|Program / Shader|对象 / 共享对象|额外结构解析耗时（单次观察）|诊断|
|---|---|---|---|---|
|Base_ATOS_Emit_FX_Detail_DetailColLayer|1916 / 557|288 / 278|0.132s|3 个跨类型绑定表重叠警告|
|VFX_Transparent_SFC_Hand|336 / 165|156 / 150|0.029s|无|
|MK_Top_Diagnostic_Forward|3672 / 711|382 / 378|0.336s|2 个跨类型绑定表重叠警告|
|MKCustomStocking|3672 / 711|382 / 378|0.354s|2 个跨类型绑定表重叠警告|

这些耗时不含旧解析器解析、进程启动、DXC 或渲染；没有作中位数或加速结论。上述警告均为真实存在的 SRV/UAV 共用绑定字节，不能作为损坏结论，也不代表可任意原地修改。

真实源码 stdio MCP 在单个客户端会话中依次打开四个固定源，验证全部新增结构查询、分页、候选 SHA256/状态稳定和源文件保护。分别通过分页取回一个共享对象的 1168、336、1848、1848 个完整引用者；重连/冻结 EXE 交付留待后续阶段。

执行与证据：

- `python -X utf8 output/reasy-p1-20261021/verify_structure.py` → `verified-corpus.json`，19 个快照通过。
- `python -X utf8 output/reasy-p1-20261021/mcp_structure.py` → `mcp-result.json`，真实 stdio 通过，stderr 保存在同目录。
- `npm run test:core` → 293 项，205 通过、88 skip，日志 `core-final.log`。
- `npm run test:mcp` → 17 项通过，日志 `mcp-tests.log`。
- `npm run test:frontend` → 17 项通过、1 项因缺少 skin Shader fixture 跳过，日志 `frontend-tests.log`。
- `npm run build` → TypeScript/Vite 构建通过；存在既有的前端 chunk 大小提示，日志 `frontend-build.log`。

新增合成检查包括巨大输入布局计数、mask/计数与总数不符、未知资源类型、非空第五类表的未验证提示、共享范围重叠、资源版本提示、分页、原字节保留、缓存身份变化，以及 Shader 尺寸变化后重新使用候选指针/长度。缺语料测试的 skip 不等于通过。未执行桌面打包、GPU 或游戏验证。

下一阶段为 P2：按最终布局执行混合批量追加，结合本索引确定完整共享影响范围，保留现有隔离 DXC、寻址证明及失败回滚约束。
