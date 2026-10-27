# MMTR Workbench MCP 0.3.0

Workbench 提供标准 stdio MCP 服务，智能体可以直接打开、查询、编辑、编译、验证和导出 MMTR/MDF。便携包自带运行环境，不需要 Python；DXC 操作仍需用户提供 `dxcompiler.dll`。

每个连接启动独立进程并拥有自己的内存文档，不读取桌面窗口的未保存内容。桌面与 MCP 复用相同编辑核心和材质布局迁移实现，文件通过另存交换。

## 连接

便携包里的 `mcp-config.json` 提供 `mcpServers` 配置。将其中 `mmtr-workbench` 条目导入支持 stdio 的客户端；配置的 command 指向本次构建目录，移动或解压到别处后改为实际 EXE 绝对路径。例如：

```json
{
  "mcpServers": {
    "mmtr-workbench": {
      "command": "D:\\Tools\\MMTR-Workbench\\mmtr-core.exe",
      "args": ["--mcp"]
    }
  }
}
```

客户端负责启动和关闭进程，无需先开桌面或手动启动服务器，不监听网络端口。客户端配置格式不同的，填写同样的 command/args。工具不会自动修改客户端配置。

替换便携包里的核心 EXE 后，需要在客户端重连 MCP，已运行的进程不会自动切换到新代码。

源码开发先运行 `npm run core:build`，把官方 SDK 安装到项目 `.build/editor-deps`；也可在当前 Python 环境运行 `python -m pip install -r requirements-editor.txt`。配置 command 为 Python 可执行文件，args 为项目中 `scripts/run_mcp.py` 的绝对路径。手动启动可用 `npm run --silent mcp`；stdio 客户端应直接调用 Python/EXE，保持 stdout 仅含协议。

## 工具

所有索引从 0 开始，路径建议使用绝对路径。列表默认每页 40 项，最多 100 项；Shader 文本默认 100 行，最多 200 行。查询返回 `total` 和下一页偏移，Shader 列表只带前 8 个通道名，可用详情继续读取。报告中的列表提供相应 `_total` 和 `_next_offset`。

|工具|用途|
|---|---|
|`workbench_status`|版本、当前文件、修改计数、未导出状态和编译结果大小|
|`workbench_configure`|设置本会话 DXC DLL 路径|
|`workbench_open`|原子打开 MMTR、MDF 或两者；默认保护待替换文档的未导出修改|
|`workbench_select_material`|切换后续操作使用的 MDF 材质|
|`workbench_list`|分页搜索 shaders、passes、metadata、materials、properties、textures|
|`workbench_inspect`|单个 Shader/通道/材质详情，分页 binding/changes 或只读 structure 报告|
|`workbench_usage`|只读统计全部 Shader 对所选 MDF 材质参数、纹理与 GPBF 的实际读取，分页返回|
|`workbench_removal_preview`|短调用启动只读删除预检，复用 material_status/result/cancel；不执行删除|
|`workbench_removal_submit`|提交通过消费者证明的有界 MMTR/MDF 删除候选；重编译、重解析后更新当前会话|
|`workbench_effect_template`|返回 `emission_multiplier` 的参数、纹理、输入/输出和 Shader 合同；不执行编译或写入|
|`workbench_effect_preview`|将效果模板动作送入批量最终布局预检；不编译 Shader 或修改文档|
|`workbench_read_shader`|分页读取 DXIL 或受限重建 HLSL|
|`workbench_export_shader`|另存完整 DXBC、DXIL、HLSL 或最近编译结果|
|`workbench_compile`|从源码字符串或文件编译，可直接严格替换指定 Shader|
|`workbench_replace_shader`|从 DXBC 或编译缓存替换，可请求核心同步接口|
|`workbench_restore_shader`|恢复某个 Shader 的打开时字节|
|`workbench_redirect`|修改通道的既有阶段引用，默认检查接口/反射|
|`workbench_rename`|在既有字符串容量内改名，保持共享名称 hash|
|`workbench_add_parameter`|自动新增尾部 UserMaterial 参数和关联 MDF 参数|
|`workbench_add_texture`|自动新增已有 PS 表中的 Texture2D 和关联 MDF 槽位|
|`workbench_material_preview`|预览一次混合追加的最终布局、关联材质和计划摘要|
|`workbench_material_submit`|使用计划摘要和请求键提交后台批次，返回 job_id|
|`workbench_material_status`|查询任务状态、阶段、数量和错误，不重建文档|
|`workbench_material_cancel`|取消未提交候选并回收本任务的 DXC 子进程|
|`workbench_material_result`|分页读取成功批次的验证和 Shader 修改报告|
|`workbench_variant_export`|显式导出已验证的独立变体 MMTR/MDF 候选对|
|`workbench_variant_discard`|释放已结束任务的独立变体候选|
|`workbench_project_save`|保存源指纹、材质身份、动作、默认值及目标路径的轻量工程|
|`workbench_project_read`|对照当前已打开的源恢复工程意图，始终要求重新规划|
|`workbench_set_master`|显式修改指定材质的 MMTR 相对路径，只影响该材质|
|`workbench_edit_mdf`|一个事务内批量修改、增删 MDF 参数/纹理及修改设置/标记|
|`workbench_validate`|DXIL/metadata/材质布局验证，分页返回问题|
|`workbench_export`|预检目标后另存 MMTR、MDF 和 JSON 报告|
|`workbench_reset`|丢弃 MMTR/MDF 内存编辑，恢复打开时原字节|

工具 schema 包含完整参数和枚举。MDF 批量动作采用 `action` 区分类型：`set_property`、`set_property_components`、`add_property`、`delete_property`、`set_texture`、`add_texture`、`delete_texture`、`set_setting`、`set_flags`、`set_master`。每项明确指定 `material_index`；最多 100 项，整批失败则恢复全部内存修改。`set_setting` 支持 shader_type、shader_lod、bake_texture_array_size；master 使用专用 `workbench_set_master` 或批次 `set_master` 动作。

### 独立变体与工程

`workbench_material_preview` 和 `workbench_material_submit` 不传 `variant` 时仍同步当前 MDF 全部已知关联材质。需要只修改所选材质时，两次调用传入相同的 `variant`：

```json
{
  "actions": [
    {"action":"parameter","name":"ExampleP","byte_size":8,"values":[0,0]},
    {"action":"texture","name":"ExampleMap","path":"systems/rendering/NullBlack.tex"}
  ],
  "record_index": 130,
  "variant": {
    "master":"MaterialShader/Variation/example.mmtr",
    "mmtr_path":"D:/Mod/natives/STM/MaterialShader/Variation/example.mmtr.240906211",
    "mdf_path":"D:/Mod/variant.mdf2.45",
    "overwrite":false
  }
}
```

路径和记录索引必须换成实际输入。目录须已存在，MMTR 输出须保留 master 的完整资源目录及源数字版本，不能只改文件名。预览返回 `scope`、`selected_material`、`known_users`、`target_materials` 和未扫描的外部范围；独立模式只同步所选材质，其它材质保持。

提交时照常附上预览的 `digest` 作为 `expected_plan` 及唯一 `request_key`。独立任务 `succeeded/stage=candidate_ready` 表示已验证的候选可用，当前 MMTR/MDF 和 revision 保持不变，也没有写出文件。取得报告后，显式调用 `workbench_variant_export({"job_id":"…"})` 写出候选对，或调用 `workbench_variant_discard` 丢弃。每会话最多保留一份候选；导出/丢弃后才能提交下一批次。重复导出同一任务只返回收据，不再次写入。源、DXC 或目标在规划后变化时拒绝导出；普通写失败回滚不保证跨文件进程崩溃原子性。

`workbench_project_save` 接受 `path`、相同的 `actions/record_index/variant` 和可选 `overwrite`，文件以 `.mmtrproj.json` 结尾。工程保存 schema、源及 DXC 内容指纹、材质身份、动作/默认值和目标；不保存资产、IR、旧偏移或可执行计划。源必须已另存且可从磁盘重开，有未保存编辑时应先另存并打开新源。

重开相同 MMTR/MDF 并选择相同材质、配置 DXC 后，`workbench_project_read({"path":"D:/Mod/material.mmtrproj.json"})` 返回 `project.request`、`changed_inputs` 和 `requires_replan=true`；它不自动打开引用或执行工程。恢复意图后重新 preview/submit，不能复用旧 digest。

### 只读结构预检

源码新增的 structure 查询在重新构建核心并重连 MCP 后可用，旧便携包不会自动获得。无需 DXC，打开 MMTR 后调用 `workbench_inspect`：

```json
{"kind":"structure","limit":20}
```

返回 header、当前候选 SHA256、对象数量和分层预检。`structure=passed` 只表示本模型没有发现结构问题；`shader_analysis=not_run`、`auto_extension=unproven`，不能代替 `workbench_validate` 或证明游戏可用。新解释发现问题时仍保留旧浏览功能。

|kind|index 与分页|
|---|---|
|`structure_program`|index 为 Program/通道索引；表内 bindings 和对象身份分页|
|`structure_objects`|分页读取物理对象；包含类别、范围、原字节摘要及前 256B 预览|
|`structure_object_users`|index 为对象列表中的 index；分页列出所有引用 Program 及指针字段位置|
|`structure_shader_users`|index 为 Shader 索引；分页列出所有引用 Program，保留 legacy lane 与实际 Shader 阶段的区别|
|`structure_metadata`|不填 index 时列描述符；填 index 时读取该描述符和分页成员|
|`structure_diagnostics`|分页诊断，包含严重度、代码、Program、绝对字段偏移|
|`structure_unmodeled_ranges`|分页列出前缀中尚未解释的区段；包含字符串和 padding，不意味着这些字节可以删除|

这里使用 `items/total/offset/next_offset` 分页格式，嵌套绑定、成员及使用者也分页。所有偏移、Program/对象索引属于返回 SHA256 对应的当前候选；编辑后应重新查询。状态 cache key 的算法为 `opaque`，未知资源类型保留原值并给出诊断。

### 后台混合追加

长任务优先使用后台接口。先打开 MMTR/关联 MDF、选择关联材质并配置 DXC，再调用 `workbench_material_preview`：

```json
{"actions":[
  {"action":"parameter","name":"ExtraParam","byte_size":8,"values":[0,0]},
  {"action":"texture","name":"ExtraMap","path":"systems/rendering/NullBlack.tex"}
],"record_index":130}
```

示例 record_index 必须替换成当前文档实际的 PS 绘制记录。预览不运行深度 Shader 编译；仍可能在执行中因未知寻址而失败。保存返回的 digest，以相同 actions/record_index 调用 `workbench_material_submit`，另加 `expected_plan=digest` 和唯一 `request_key`。

提交返回 job_id，随后用 `workbench_material_status` 查询。状态为 queued、running、cancelling、succeeded、failed、cancelled 或 stale；stage、completed/total 表示当前阶段进度，不是全任务 ETA。成功后调用 `workbench_material_result`，Shader 报告用 offset/limit 分页。文档只在全部验证完成后提交一次，导出仍需显式调用原有 export。

同会话只运行一个修改任务。重试必须使用原 request_key、动作和摘要，包括已经完成的任务；同键不同请求会拒绝。会话最多保留128个任务，不淘汰幂等键，重启后不恢复。读取返回已提交状态；期间发生编辑、材质切换或 DXC 改变，会拒绝过期候选，保留较新的编辑。

取消只回收候选 worker，不改当前文档或写入文件。cancel 返回 cancelling 后继续查询，直到 cancelled；若提交先完成则返回 succeeded。Windows worker 归属于 kill-on-close Job Object，冻结入口另监视 onefile 启动器，外层被终止时也结束内层核心。旧单项 MCP 工具仍兼容同步调用。桌面“参数与资源”的批量图标、参数/SRV 新增入口共用该后台任务服务。

P3 本机验收使用固定 Stocking 副本：源码和冻结 stdio 均通过取消不变、过期拒绝、混合成功、运行中及成功后请求重试、成功后取消仍返回成功；状态查询最大延时分别为0.282/0.344秒，不能作为其它机器的耗时保证。Windows onefile 外层强制结束的进程树回收另行通过。桌面构建中的自有 HLSL 混合批次通过并自动定位新增参数，源码桌面使用 Stocking 验证进度与取消。均未进行游戏验证或自动安装。

## 调用示例

先打开 MMTR 和 MDF，再搜索实际关联的材质。材质列表在打开 MMTR 时默认 `linked_only=true`，按各材质自己的 master 精确匹配。

```json
{"mmtr_path":"D:/Assets/example.mmtr.240906211","mdf_path":"D:/Assets/example.mdf2.45","material_index":5}
```

上面是 `workbench_open` 参数。然后调用 `workbench_list`：

```json
{"kind":"materials","query":"Pants","limit":20}
```

批量编辑当前 MDF 两项已有字段，调用 `workbench_edit_mdf`：

```json
{"actions":[
  {"action":"set_property","material_index":5,"name":"ColorParam","values":[1,1,1,1]},
  {"action":"set_texture","material_index":5,"slot":0,"path":"systems/rendering/NullBlack.tex"}
]}
```

字段名、值数量和槽位必须从当前文档查询，不假定每份 MDF 都有示例字段。`metadata` 列表可查询当前 MMTR 的 UserMaterial 成员和 SRV，供 MDF 新增已有接口时选择。

新增真正的无效果参数，调用 `workbench_add_parameter`：

```json
{"name":"TestParam","byte_size":16,"material_index":5,"values":[0,0,0,0]}
```

参数偏移由当前 UserMaterial 大小自动确定。新增无效果贴图接口调用 `workbench_add_texture`，其中 record_index 必须从当前通道选择，不能套用其它 MMTR 的序号：

```json
{"name":"TestTexture","record_index":97,"material_index":5,"path":"systems/rendering/NullBlack.tex"}
```

两项操作默认同步所选 MDF 材质；MMTR、编译或 MDF 任一步失败都恢复操作前状态。然后调用 `workbench_validate`，通过后调用 `workbench_export`：

```json
{"mmtr_path":"D:/Output/example.mmtr.240906211","mdf_path":"D:/Output/example.mdf2.45","report_path":"D:/Output/report.json"}
```

将材质改用独立MMTR时，先完成参数/纹理新增，再显式调用 `workbench_set_master`，例如 `{"material_index":5,"path":"MaterialShader/Variation/test.mmtr"}`。路径必须位于MaterialShader目录下、以.mmtr结尾且不带版本后缀。它不复制MMTR、不自动改变其它同master材质，也不证明任意目标Shader兼容。引用修改后可导出新MMTR并重新打开其实际路径，确认关联材质。

输出目录需存在，目标不能覆盖已打开源文件。导出先构造/验证全部候选并预检所有路径，写入使用原子替换；后续写入失败会尝试恢复先前目标。普通 I/O 故障在回归中验证恢复，不承诺断电或外部进程并发写入时的跨文件原子性。响应包含绝对路径、大小和 SHA256，成功后更新会话的未导出状态。

## 编辑范围与验证

自动参数/纹理新增沿用 Workbench 的有界迁移：关联 MDF 证明布局，参数仅支持 UserMaterial 尾部 4/8/12/16 字节，纹理支持已识别 PS 表中的固定 float4 Texture2D，以及槽身份可证明的原生动态 Texture2D；原生 DXC 在独立进程运行。不能确认的寻址、多个关联材质布局不一致、新阶段/未知资源表等会整体拒绝。`sync_mdf=false` 只跳过向所选 MDF 加字段，仍需打开关联 MDF 证明原布局。

固定路径在 space0 有限 SRV 尾部分配，保留其它 space 的原声明；space0 无界、范围重叠或缺少单纹理模板时仍拒绝。原生路径核对无界 `bindlessTexture2D` 的反射和实际绑定，支持旧式 `createHandle`、`createHandleFromBinding`，以及已注解的资源堆 Texture2D；不硬编码 space。索引必须直接来自 MDF 纹理尾部槽，允许加零，参数／GPBF 作为纹理索引或未知算术会拒绝。

`record_index` 可选择上述任一路径已证明的材质 PS 记录；原生记录的 SRV 名称不必列出 MDF 纹理名。同一母版可含分别使用固定或动态路径的不同 PS，同一 PS 混用两种材质纹理路径或被其它阶段共享则拒绝。扩展器按策略闭合共享引用，原生记录保留已有资源表和动态绑定，不追加固定 SRV。连续新增会迁移既有纹理／GPBF 地址，并为每轮迁移分配独立 SSA 名称；新增槽本身不自动被原 Shader 采样。

新版布局标记只在 MDF 标量／纹理／GPBF 计数、16 字节对齐的尾部地址、唯一整数比较消费者相互印证时迁移。保留比较和控制流，更新地址与比较值；未知尾部读取、额外消费者或计数不符会整体回滚。该识别用于追加接口，不能作为删除资格或游戏兼容证明。

同一 MMTR 的其它材质使用者也必须匹配新布局。服务不会自动补齐全部使用者或改变 master；独立材质实验可用 `workbench_set_master` 显式设置独立 master，再打开与路径匹配的 MMTR。仅改输出文件名不等于 MDF 已改用该文件。

## 材质读取统计

`workbench_usage` 需要打开关联 MDF 并设置 DXC，在材质工作进程中反汇编每个不同载荷的 Shader，不修改文档。`kind` 选择 `parameters`、`textures`、`gpbf` 或 `failures`，`state_filter` 按状态筛选：

- `used`：至少一个 Shader 读取。参数按读取字节落在参数偏移范围内判断；纹理要求句柄值流入 `createHandleFromHeap`，或按名称绑定的 SRV 被使用。
- `descriptor`：读取了纹理描述符值，但未构造为资源句柄（例如只参与反馈或上报），槽位仍是有效的材质输入。
- `unused`：在当前识别器覆盖的 DXIL 读取形式中未发现消费者，且 `complete` 对应类别为 true；这不是完整的未使用证明。
- `unknown`：存在无法证明身份的读取、静态 `UserMaterial` 未支持的访问或句柄逃逸、资源句柄合流、追踪超限或反汇编失败；`failures` 列出对应 Shader 与原因。

读取来源与地址迁移一致：heap 材质 CB、BindlessBuffer、UserMaterialInstances，另加静态绑定的 `UserMaterial` 常量缓冲与按名称绑定的纹理。无消费者的 `extractvalue` 不计入。每个函数独立解析 SSA；相同载荷只分析一次，但 `shader_count` 统计实际 Shader 条目。材质含 MMTRS（shader LOD 重定向）时，未按重定向证明的尾部偏移不归到具体字段，按名称证明的 SRV 仍可报告。缓存绑定 MMTR/MDF 字节、材质及 DXC 路径和内容，DLL 原路径被替换也会失效。

此工具对应优化计划 P5 的消费者证明，预检返回的 `coverage.scope=bounded_dxil_consumers` 和 `coverage.deletion_supported=false` 明确表示它仍是只读证明；只有 `workbench_removal_submit` 的执行报告才会在完整候选验证成功后标记 `deletion_supported=true`。两者都不证明实际运行分支、所有外部使用者或游戏效果。报告不包含 IR，未知/间接访问仍拒绝。

`workbench_removal_preview` 在已知全部关联材质布局一致时启动只读后台任务。输入 `actions=[{"action":"parameter","name":"UnusedP"},{"action":"texture","name":"UnusedMap"}]`、非空 `request_key` 和可选 `material_index`；`workbench_removal_submit` 使用预览返回的 `plan_digest` 和新的请求键提交候选。两者查询/取消复用现有材质任务接口。结果 `eligible=true` 才能进入删除执行；已用、仅读取描述符、未知访问、句柄逃逸、跨函数调用和 MMTRS 阻止资格。执行器同步声明、STAT、绑定表、计数和已知 MDF，并在失败时不更新当前会话。独立变体可传既有变体目标，提交后仍需显式导出。逐 Shader 身份、失败与最终收缩布局见分页结果。

`workbench_effect_template(name="emission_multiplier")` 返回封闭的 P6 合同：新增 `MMTR_EmissionStrength` 和 `MMTR_EmissionMap`，输入是原像素输出与这两个接口，输出是最终像素输出。`workbench_effect_preview` 将同一动作送入批量最终布局计划；调用方必须提供显式读取并保留原受光/透明分支的 Shader，再通过批量接口编译验证。状态编辑、任意节点图和 GPU 预览仍未开放。

2026-10-21 的 PR #3 整合验证：核心 330 项中 242 通过、88 项可选语料跳过，MCP 19 项通过，前端 17 项通过/1 项缺 fixture 跳过，TypeScript/Vite 构建通过。自有 HLSL 通过隔离 DXC 验证 shl 步长和常量地址在参数/纹理混合批次后的实际读取。固定 P0 Stocking 输入的真实源码 stdio 查询首次约 15.40 秒，后续分页约 0.04 秒；报告识别 164 个参数、21 个纹理、4 个 GPBF 有读取，11 个参数为 unknown，整体覆盖未完成，不能推断这些参数未使用。会话 revision、文档状态与输入文件字节均保持不变。证据仅限源码/CPU/DXC；未验收冻结包、GPU 或游戏效果。本地复现记录位于忽略的 `output/pr3-*`，不是线上附件。

删除 MDF 字段不代表删除 MMTR Shader 接口；Sampler/UAV 和真正 MMTR 接口删除不提供自动迁移。手动 rename、expert 替换等仍受既有核心边界限制，不等于可运行布局已成立。`validate` 证明本次静态接口契约，不能替代 GPU 或游戏画面验收。

开发验证：`npm run test:mcp` 运行无游戏资产的 SDK 工具契约、分页、批量回滚、源文件/硬链接保护、导出故障恢复测试。真实 stdio 客户端使用 `scripts/smoke_workbench_mcp.py --help` 查看输入参数；可分别连接源码和冻结 EXE，比较相同输入的候选 hash。离线实验输出仅保留本地，不复制到游戏目录。

## 性能与长调用

真正新增 MMTR 接口需要分析全部 Shader，并重建受影响的载荷，耗时取决于文件和修改量。MDF 已有字段编辑与列表查询不需要执行这套迁移流程。客户端的工具调用超时应覆盖实际操作耗时；核心材质工作进程自身上限为 300 秒。

扩展器对同一轮 IR 复用已解析的读取和反射，按行重写受影响的取值。重写后仍独立解析实际候选，编译后继续核对字段身份、签名、绑定和完整性。

同一会话按全部编辑值缓存重建结果及已解析结构。验证结论还绑定 DXC 路径和 DLL 内容；Shader、元数据、迁移证明、引用或 DXC 改变后均重新检查。自动扩展完成后立即调用 `validate` 或 `export` 可以复用已验证结果。重新打开导出文件时，内存缓存不会写入资产或跨进程复用。

旧 `workbench_add_parameter/add_texture` 仍是同步长调用。混合批次和独立变体优先使用 `workbench_material_preview/submit/status/cancel/result`；短提交与查询避免长调用结果不明，原生迁移和编译仍串行执行。超时重试沿用原请求键和内容，不能换键重复追加。更大的文件仍可能需要较长时间，不能把某个本地样本的耗时当作全部 MMTR 的上限。
