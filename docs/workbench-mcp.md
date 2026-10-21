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
|`workbench_read_shader`|分页读取 DXIL 或受限重建 HLSL|
|`workbench_export_shader`|另存完整 DXBC、DXIL、HLSL 或最近编译结果|
|`workbench_compile`|从源码字符串或文件编译，可直接严格替换指定 Shader|
|`workbench_replace_shader`|从 DXBC 或编译缓存替换，可请求核心同步接口|
|`workbench_restore_shader`|恢复某个 Shader 的打开时字节|
|`workbench_redirect`|修改通道的既有阶段引用，默认检查接口/反射|
|`workbench_rename`|在既有字符串容量内改名，保持共享名称 hash|
|`workbench_add_parameter`|自动新增尾部 UserMaterial 参数和关联 MDF 参数|
|`workbench_add_texture`|自动新增已有 PS 表中的 Texture2D 和关联 MDF 槽位|
|`workbench_set_master`|显式修改指定材质的 MMTR 相对路径，只影响该材质|
|`workbench_edit_mdf`|一个事务内批量修改、增删 MDF 参数/纹理及修改设置/标记|
|`workbench_validate`|DXIL/metadata/材质布局验证，分页返回问题|
|`workbench_export`|预检目标后另存 MMTR、MDF 和 JSON 报告|
|`workbench_reset`|丢弃 MMTR/MDF 内存编辑，恢复打开时原字节|

工具 schema 包含完整参数和枚举。MDF 批量动作采用 `action` 区分类型：`set_property`、`set_property_components`、`add_property`、`delete_property`、`set_texture`、`add_texture`、`delete_texture`、`set_setting`、`set_flags`、`set_master`。每项明确指定 `material_index`；最多 100 项，整批失败则恢复全部内存修改。`set_setting` 支持 shader_type、shader_lod、bake_texture_array_size；master 使用专用 `workbench_set_master` 或批次 `set_master` 动作。

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

这里使用 `items/total/offset/next_offset` 分页格式，嵌套绑定、成员及使用者也分页。所有偏移、Program/对象索引属于返回 SHA256 对应的当前候选；编辑后应重新查询。状态 cache key 的算法为 `opaque`，未知资源类型保留原值并给出诊断。结构模型和限制详见[结构检查](structure-inspection.md)。

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

自动参数/纹理新增沿用 Workbench 的有界迁移：关联 MDF 证明布局，参数仅支持 UserMaterial 尾部 4/8/12/16 字节，纹理仅支持已识别 PS 表中的 float4 Texture2D；原生 DXC 在独立进程运行。不能确认的寻址、多个关联材质布局不一致、新阶段/空表等会整体拒绝。`sync_mdf=false` 只跳过向所选 MDF 加字段，仍需打开关联 MDF 证明原布局。

PS声明可以混有其它space的有限或无界SRV数组：这些声明保留原样，新增单个Texture2D只在space0有限范围末尾分配。space0本身无界、范围重叠或没有单纹理模板时仍拒绝。记录仍应选择包含MDF材质纹理名的PS表；仅有引擎资源的堆句柄绘制记录不能直接作为record_index。扩展器会同时检查并迁移已识别的堆材质纹理/GPBF读取，新增槽本身不自动被原Shader采样。连续新增会为每轮地址迁移分配独立临时变量名，可先加纹理再加参数。

同一 MMTR 的其它材质使用者也必须匹配新布局。服务不会自动补齐全部使用者或改变 master；独立材质实验可用 `workbench_set_master` 显式设置独立 master，再打开与路径匹配的 MMTR。仅改输出文件名不等于 MDF 已改用该文件。

## 材质读取统计

`workbench_usage` 需要打开关联 MDF 并设置 DXC，在材质工作进程中反汇编每个不同载荷的 Shader，不修改文档。`kind` 选择 `parameters`、`textures`、`gpbf` 或 `failures`，`state_filter` 按状态筛选：

- `used`：至少一个 Shader 读取。参数按读取字节落在参数偏移范围内判断；纹理要求句柄值流入 `createHandleFromHeap`，或按名称绑定的 SRV 被使用。
- `descriptor`：读取了纹理描述符值，但未构造为资源句柄（例如只参与反馈或上报），槽位仍是有效的材质输入。
- `unused`：全部 Shader 都不读取。只有 `complete` 对应类别为 true 时才给出。
- `unknown`：存在无法证明身份的读取（与地址迁移的拒绝条件相同）、静态 `UserMaterial` 动态行索引或反汇编失败；`failures` 列出对应 Shader 与原因。

读取来源与地址迁移一致：heap 材质 CB、BindlessBuffer、UserMaterialInstances，另加静态绑定的 `UserMaterial` 常量缓冲与按名称绑定的纹理。无消费者的 `extractvalue` 不计入。材质含 MMTRS（shader LOD 重定向）时，尾部字段位置未按重定向解析，纹理与 GPBF 标为不完整。同一 MMTR/MDF 字节、材质与 DXC 路径复用一份结果；用于判断可复用的参数行与纹理槽，或确认改写后的 Shader 确实读取新增参数。统计不证明运行时实际经过的分支。

删除 MDF 字段不代表删除 MMTR Shader 接口；Sampler/UAV 和真正 MMTR 接口删除不提供自动迁移。手动 rename、expert 替换等仍受既有核心边界限制，不等于可运行布局已成立。`validate` 证明本次静态接口契约，不能替代 GPU 或游戏画面验收。

开发验证：`npm run test:mcp` 运行无游戏资产的 SDK 工具契约、分页、批量回滚、源文件/硬链接保护、导出故障恢复测试。真实 stdio 客户端使用 `scripts/smoke_workbench_mcp.py --help` 查看输入参数；可分别连接源码和冻结 EXE，比较相同输入的候选 hash。离线实验输出仅保留本地，不复制到游戏目录。

## 性能与长调用

真正新增 MMTR 接口需要分析全部 Shader，并重建受影响的载荷，耗时取决于文件和修改量。MDF 已有字段编辑与列表查询不需要执行这套迁移流程。客户端的工具调用超时应覆盖实际操作耗时；核心材质工作进程自身上限为 300 秒。

扩展器对同一轮 IR 复用已解析的读取和反射，按行重写受影响的取值。重写后仍独立解析实际候选，编译后继续核对字段身份、签名、绑定和完整性。

同一会话按全部编辑值缓存重建结果及已解析结构。验证结论还绑定 DXC 路径和 DLL 内容；Shader、元数据、迁移证明、引用或 DXC 改变后均重新检查。自动扩展完成后立即调用 `validate` 或 `export` 可以复用已验证结果。重新打开导出文件时，内存缓存不会写入资产或跨进程复用。

接口新增仍是同步、串行工具；本次优化没有新增批量 MMTR 接口工具或后台任务。超时后先核对会话状态和新增名称，避免在操作尚未结束时重复提交。更大的文件仍可能需要较长时间，不能把某个本地样本的耗时当作全部 MMTR 的上限。
