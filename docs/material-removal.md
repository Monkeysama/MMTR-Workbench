# P5：材质接口删除

当前是布局迁移和消费者证明节点。删除预检只读，`eligible` 仅表示本档 DXIL 分析没有发现阻塞，`coverage.deletion_supported=false`。尚未交付 MMTR/MDF 删除执行、候选验证或游戏验收。

假设：在全部 Shader 消费者可证明的条件下，按字段身份建立一次最终收缩布局，能够保留旧参数、纹理和 GPBF 的值来源。未使用统计不能独立证明可以删除；未知句柄、跨函数传递、间接寻址和 MMTRS 都必须阻止本档删除。

最小实验：两个参数和两个纹理的合成布局，删除中间及末尾无消费者字段，比较保留字段的逐字偏移、实例步长与 GPBF 身份；原生自有 HLSL 的已用字段必须拒绝，追加无作用字段应通过消费者预检。任务运行中查询、取消、重试和源身份变化分别检查。通过判据是保留取值身份一致、未知/已用/描述符读取不被授予资格、文档 revision 和资产字节不变。当前停止点为只读后台预检和 IR 迁移，不执行 metadata、STAT、资源表或 MDF 写回，不安装游戏。

## 支持边界

- `UserMaterial` 为非空 16B 对齐参数区，成员是单行内的 float1..4；删除后保留至少一个参数。
- 参数删除按原偏移顺序紧密排列且不跨 16B 行；只删纹理时保留参数原位置。纹理和 GPBF 以原名称及顺序建立稳定映射，GPBF 不可删除。
- 仅接受已证明的 heap CB、BindlessBuffer、UserMaterialInstances 和静态 UserMaterial 访问。已使用/仅读取描述符/未知字段均拒绝；不通过清除 metadata 规避 Shader 消费者。
- 全部不同载荷只分析一次，但每个 Shader 的阶段、索引和载荷 SHA256 均保留。全部已知关联材质从当前字节重新解析并核对；未扫描的外部使用者明确为 `not_scanned`。
- DXC 在隔离子进程中执行；后台任务快照和结果验证短暂持锁，计算不锁住当前文档。共用进度、取消、请求键去重、退出回收、源/MMTR/MDF/DXC 身份过期检查。结果不提交文档，不推进 revision。

## MCP 与桌面协议

`workbench_removal_preview(actions, request_key, material_index?)` 返回短任务状态。动作只含 `action=parameter|texture` 和 `name`，最多 100 项；重试沿用相同请求键及参数。使用已有 `workbench_material_status`、`workbench_material_result` 和 `workbench_material_cancel` 查询、分页或取消。`result` 中 `eligible=false` 是分析完成但存在消费者/覆盖阻塞，不是 worker 崩溃；任务 `failed` 表示无法分析。分页分别提供 Shader 与失败总数，不返回 IR。

桌面 RPC 以 `material_removal_preview` 提交同一后台服务，使用已有任务查询接口。本节点尚未添加删除按钮；现有手动 metadata 删除不提供运行时安全保证。

## 后续验收

1. 同步可执行声明和 STAT，重建资源绑定双表及计数，显式迁移 MDF 参数偏移和值，保持未知字节。
2. 全部受影响 Shader 重新编译、重解析并证明保留字段与资源身份；保持候选事务、源文件/硬链接保护和失败回滚。
3. 与共享 master、独立变体和工程意图接通，分别验证中间与尾部删除的源码/冻结 MCP/桌面行为。
4. 对应真实游戏用例通过后，才宣称该删除场景游戏可用。当前无 GPU 或游戏删除证据。

## 首个检查点验证

`python scripts/test_core.py`：372 项中 284 通过、88 项缺可选本地语料跳过。`python scripts/test_mcp.py`：22 项通过。删除专项 13 项包含真实隔离 DXC：自有 HLSL 新增无作用参数/纹理后预检通过，原 BaseMap 仍拒绝；两份相同 Shader 仅分析一次而逐项保留身份，revision 和源字节不变。数字 SSA 地址迁移候选经汇编和验证，保留字读取偏移为 0/4/8/12；该实验未改变声明，保留原 STAT 只用于地址实验，不是完整删除候选。进程取消、超时回收、分页、去重及 MMTR/MDF/DXC 身份过期测试通过。日志位于忽略的 output/p5-*，没有 GPU/游戏证据。
