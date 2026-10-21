# P5：材质接口删除

当前已完成有界删除候选事务。`eligible` 表示消费者证明通过，提交会在隔离 DXC 中重写可执行/STAT、重建 MMTR 表和计数、同步已知关联 MDF，并重开验证；`coverage.deletion_supported=true` 只对该候选执行器成立。没有 GPU 或真实游戏验收。

假设：在全部 Shader 消费者可证明的条件下，按字段身份建立一次最终收缩布局，能够保留旧参数、纹理和 GPBF 的值来源。未使用统计不能独立证明可以删除；未知句柄、跨函数传递、间接寻址和 MMTRS 都必须阻止本档删除。

最小实验：两个参数和两个纹理的合成布局，删除中间及末尾无消费者字段，比较保留字段的逐字偏移、实例步长与 GPBF 身份；原生自有 HLSL 的已用字段必须拒绝，追加无作用字段应通过消费者预检。任务运行中查询、取消、重试和源身份变化分别检查。通过判据是保留取值身份一致、未知/已用/描述符读取不被授予资格、文档 revision 和资产字节在失败时不变；成功任务只更新当前内存候选，导出仍由既有显式变体流程负责，不安装游戏。

## 支持边界

- `UserMaterial` 为非空 16B 对齐参数区，成员是单行内的 float1..4；删除后保留至少一个参数。
- 参数删除按原偏移顺序紧密排列且不跨 16B 行；只删纹理时保留参数原位置。纹理和 GPBF 以原名称及顺序建立稳定映射，GPBF 不可删除。
- 仅接受已证明的 heap CB、BindlessBuffer、UserMaterialInstances 和静态 UserMaterial 访问。已使用/仅读取描述符/未知字段均拒绝；不通过清除 metadata 规避 Shader 消费者。
- 全部不同载荷只分析一次，但每个 Shader 的阶段、索引和载荷 SHA256 均保留。全部已知关联材质从当前字节重新解析并核对；未扫描的外部使用者明确为 `not_scanned`。
- DXC 在隔离子进程中执行；后台任务快照和结果验证短暂持锁，计算不锁住当前文档。共用进度、取消、请求键去重、退出回收、源/MMTR/MDF/DXC 身份过期检查。只读预检不提交文档、不推进 revision；共享删除成功后推进一次 revision，独立变体保留候选并等待显式导出。

## MCP 与桌面协议

`workbench_removal_preview(actions, request_key, material_index?)` 返回短任务状态。动作只含 `action=parameter|texture` 和 `name`，最多 100 项；重试沿用相同请求键及参数。使用已有 `workbench_material_status`、`workbench_material_result` 和 `workbench_material_cancel` 查询、分页或取消。`result` 中 `eligible=false` 是分析完成但存在消费者/覆盖阻塞，不是 worker 崩溃；任务 `failed` 表示无法分析。分页分别提供 Shader 与失败总数，不返回 IR。

桌面 RPC 以 `material_removal_preview` / `material_removal_submit` 接入同一后台服务，桌面元数据面板的参数和 SRV 删除按钮均先打开消费者证明对话框；旧 metadata-only 删除路径不再由这些按钮调用。独立变体删除复用 P4 的候选导出、目标保护和双文件回滚。

## 后续验收

1. 已完成可执行声明和 STAT、资源绑定双表/计数、MDF 参数偏移和值迁移，并保持未知字节。
2. 已完成受影响 Shader 重编译、重解析、保留字段/资源身份证明、候选事务和失败回滚。
3. 已接通共享 master、独立变体、MCP/RPC 和桌面对话框；中间/尾部删除与继续追加均有隔离 DXC 合成测试。
4. 真实游戏用例仍待执行，不能把本节点称为游戏可用。

## 首个检查点验证

`python scripts/test_core.py`：380 项中 292 项通过、88 项缺可选语料跳过；删除专项 6 项包含真实隔离 DXC，覆盖中间/末尾参数与纹理、活动字段迁移、资源计数、共享 master、独立变体候选导出、失败回滚和继续追加。桌面 `npm run build` 与前端测试通过。没有 GPU/真实游戏证据。
