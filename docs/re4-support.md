# RE4 支持

2026-10-28。结论：RE4（BIOHAZARD RE:4）的 MMTR `.mmtr.221007879` 与 MDF `.mdf2.32` 可以打开、浏览、编辑 MDF、新增 UserMaterial 参数、替换 Shader 并另存；原样另存逐字节一致。新增 Texture2D 仍被拒绝，见第四节。除第五节的一次游戏内测试外，均为 CPU 静态证据。

## 一、格式差异

| 项 | 荒野 | RE4 |
|---|---|---|
| MMTR 资源版本 | 240906211 | 221007879 |
| SDF 记录长度 | 0x110 | 0x108 |
| 组数（header +4） | 4 或 5 | 3 |
| compute 线程组 / 拓扑 | 记录 +0x108 / +0x10E | 记录 +0x100 / +0x106 |
| MDF 版本 | 2.45 | 2.32 |
| master 根目录 | `MaterialShader/` | `_Chainsaw/MasterMaterial/` |
| UserMaterial 保留成员 | `CAPCOM_MATERIAL_RESERVE0..` | 另有不带编号的 `CAPCOM_MATERIAL_RESERVE` |

- 两种记录在 0xFF 之前逐字段相同：名称、7 个 Shader 指针、状态对象指针、5 组绑定/metadata 表指针、Shader 长度、各阶段计数与表计数。荒野在 0x100..0x107 多出 8 字节，原版 908505 条记录中全部为零，用途未知；之后的线程组与拓扑随之后移。
- Shader 块的 8 字节尾部同为 `xxh64(块, 0xffffffff)`。
- RE4 原版材质没有 MMTRS 表（指针为 0）。MDF 2.32 的材质项（100 字节）、属性头与纹理头布局与 2.45 相同。

## 二、识别规则

- 记录长度按文件名的资源版本选择（`inspect_mmtr.FORMATS`），组数按游戏检查。
- 无版本后缀或未登记版本时，依次尝试已知记录长度，只接受恰好一种能完整解析的文件；都失败时报荒野布局的错误，都成功时拒绝。
- MDF 版本取文件名后缀，只接受 32 与 45。master 根目录与导出后缀按打开的 MDF 版本校验。

## 三、验证

输入：RE4 Steam 版 `re_chunk_000.pak` 与 patch_001..006（2026-10-16），按 `RE4_STM.list` 解出。

| 检查 | 输入 | 结果 |
|---|---|---|
| 解析、结构检查 | `_chainsaw/mastermaterial` 下全部 153 个 MMTR | 全部解析；结构诊断无错误（148 条 `object_overlap` 警告） |
| 原样另存 | 同上 | 153 个逐字节一致 |
| MDF 解析 | `_chainsaw/character` 下全部 655 个 mdf2.32 | 全部解析 |
| 荒野回归 | 荒野 `MaterialShader/Variation` 下 466 个 MMTR，与 `origin/main` 逐文件对比 | 解析结果、结构诊断、打开与原样另存相同；13 个文件改动前后都被组数检查拒绝，只有提示措辞不同；1 个文件改动前后都解析失败 |
| 材质读取识别 | 荒野 466 个模板与 RE4 153 个模板中的全部不同 Shader | 荒野 124191 个的 `material_reads` 结果与 `origin/main` 相同；RE4 34473 个全部识别，无报错 |
| stdio MCP | `Character_Detail_Expensive_8weight` + 关联 mdf2.32 | 打开、binding、metadata、passes、DXIL 与 HLSL 读取、MDF 参数编辑、新增参数、验证、导出与重开通过；新增贴图被拒绝（第四节） |
| 合成测试 | `tests/test_re4_support.py` | 记录长度与版本、组数、线程组偏移、Shader 重定位与重开、metadata 编辑、MDF 2.32 与 master 根目录、保留成员、网格表读取 |

`npm run test:core`（配置 DXC）413 项通过、153 项因缺语料跳过；`npm run test:mcp` 22 项通过；`npm run test:frontend` 29 项通过、1 项跳过；`npm run build` 通过。

## 四、新增参数与贴图

新增 UserMaterial 尾部参数可用；新增 Texture2D 仍被拒绝。

### 网格表读取

RE4 的可见性缓冲与网格变体经 `Instances` 读取 BindlessBuffer 中的网格表：

```text
行 = ((((X >> 16) & 255) + 1) >> 1) + Instances[i].字节52 / 16
```

表头 8 字节，之后每项 8 字节，按项号奇偶取 x 或 z；读出的子网格记录地址再 `>> 4`，读取其后的 +2～+7 行。原版 153 个模板中有 789 个不同 Shader 含这一形态（PS 699、VS 75、MS 15），均为同一结构。旧实现把所有 BindlessBuffer 读取都当作材质读取，遇到该地址报「未知材质表基址」，新增参数整体回滚。

`material_addresses._mesh_table_reads` 只把这一确切形态，以及只以其读出值（`extractvalue`，或两者之间的 `select`）为基址的链式读取，排除出材质读取。`Instances` 的元素偏移不是 52、缓冲不是 `Instances`、或链式基址混入其它值时，按原规则识别或拒绝。网格表的用途是推断，依据是地址来源与数据流；未在运行时核对。

### 结果

`Character_Detail_Expensive_8weight` 与 Shinano 的 `cha000_00.mdf2.32`（材质 0）经源码 stdio MCP：

- `add_parameter` 16 字节，偏移 608，接口验证通过；
- 导出后重开：绑定报告为 `compatible_with_reserves`，新参数在 MDF 中，再次验证通过；
- 源文件未改变。

### 新增贴图

`add_texture` 被拒绝：「材质纹理表包含未支持的计算阶段或没有PS」。RE4 的预变换计算着色器（`PreTransform*VFXCS`）读取材质贴图 `Noise3D`、`Wind_MaskMap`，而接口扩展不处理计算阶段的资源表。这是现有功能的范围限制，与 RE4 格式无关；需要新贴图时复用模板中开关为 0 时不读取的槽。

## 五、游戏内测试

2026-10-28，RE4 Steam 版，玩家角色 Leon 槽位的自制 mod（31 个 mdf2、76 个材质，原模板均为 `Character_Detail_Expensive_8weight`）。

- 候选模板：原生模板经 `add_parameter` 新增 float4 `LIL_TestTint`（偏移 608，Shader 不读取；迁移改动 465 个 Shader），导出后重新打开，再把 36 个输出 4 个渲染目标且读取材质数据的 PS 的 SV_Target1.rgb 乘以 (1, 0.25, 0.25)，以普通模式替换；验证通过后另存为新文件名。
- MDF：各材质的 master 改指向候选模板，补上 `LIL_TestTint`；其余参数、纹理与 flags 不变。
- 结果：材质整体偏红，贴图细节保留，没有消失、变黑或闪退。

据此：RE4 运行时接受替换后的 Shader 块与扩展后的 UserMaterial 布局；SV_Target1.rgb 是底色，这组 36 个 PS 是该材质实际使用的 GBuffer 变体。只看了一次目测效果，没有核对哪些场景、哪些通道（阴影、前向、光追）用到了未修改的变体。

## 六、局限

- `smoke_workbench_mcp.py` 自 0.3.0 起未随工具数量更新（断言 20 个工具，现为 34 个），未用于本次验证。
