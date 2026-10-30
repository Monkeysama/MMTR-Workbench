# 鬼武者支持

2026-10-29。结论：鬼武者（Onimusha: Way of the Sword）的 MMTR `.mmtr.260605155` 与 MDF `.mdf2.51` 可以打开、浏览、编辑 MDF、新增 UserMaterial 参数与 Texture2D、替换 Shader 并另存；原版模板原样另存逐字节一致。扩展后的模板在游戏内加载并生效（见第五节）。

## 一、格式差异

| 项 | 荒野 | 鬼武者 |
|---|---|---|
| MMTR 资源版本 | 240906211 | 260605155 |
| 文件头 | 16 字节：组数、组内记录数、Shader 区起点（+8） | 24 字节：组数、各组记录数（每组一个 u16，补零到 +0x10）、Shader 区起点（+0x10） |
| 记录起点 / 长度 | 0x10 / 0x110 | 0x18 / 0x110 |
| 组数 | 4 或 5 | 4（原版全部） |
| MDF 版本 / 材质项 | 2.45 / 100 字节 | 2.51 / 104 字节 |
| master 根目录 | `MaterialShader/` | `MaterialShader/` |
| 材质数据（BindlessBuffer、heap CBV） | 第 0 行起为 UserMaterial，尾部之后一行为模板 ID 标记 | 第 0 行 lane 0 为模板 ID 标记，UserMaterial 从第 1 行开始 |

- MDF 2.51 的材质项在属性数之后多一个 u32：属性数据末端的 float 数（不含末尾对齐），其后各字段后移 4 字节。原版 179 个材质全部满足「该值 × 4 = 最后一个属性的末端」。结构编辑后按新属性区重写该值。RE9 等同为 2.51 的其他材质项布局未登记。
- 模板 ID 标记的编码与荒野相同：参数分量数 | 贴图数 << 10 | GPBF 数 << 18（例：`V_Chara_PL_Cloth_ALP` 为 172 | 27 << 10 | 2 << 18 = 552108）。贴图与 GPBF 索引表紧接 UserMaterial 之后（第 1 + 参数字节数 / 16 行起）。
- 参数实例流（`UserMaterialInstances`）与具名 UserMaterial 常量缓冲不含标记行，偏移与参数区相同。
- 可见性缓冲与网格变体经 `Instances` 读取 BindlessBuffer 中的网格表：地址（字节）= ((((X >> 16) & 255) + 1) >> 1) << 4 + `Instances[i]` 字节 52，再 >> 4 为行（RE4 为行号直接相加）。读出的 lane 是材质数据地址，其后的读取是材质读取。
- 原版 PS 的 UserMaterial 反射可能省略未读取的成员（例：`V_Chara_PL_Cloth_ALP` 的阴影 PS 只列出 130 个成员中的 125 个），偏移与 metadata 一致。

## 二、实现

- `inspect_mmtr.FORMATS` 增加文件头长度；Shader 区起点字段位置由文件头长度决定，写入方（参数、贴图、删除、Shader 替换）共用 `info['shader_start_field']`。
- `MATERIAL_HEADER_BYTES` 按游戏给出材质数据标记行长度（鬼武者 16）。`material_reads` 把 BindlessBuffer 与 heap CBV 读取的偏移换算到参数区起点，标记行内的读取为负偏移；迁移写回时加回标记行。`MaterialTailLayout.header_bytes` 不为 0 时，标记位于偏移 −16，新增参数或贴图只更新比较值，不移动读取。
- 打开文档时按游戏设置标记行长度（进程内全局）；同一进程同时只编辑一个游戏的模板。
- 新增参数的反射验证：UserMaterial 反射缺少原有成员、其余成员与新增成员偏移一致时通过；新增成员缺失或偏移不同仍报错。
- MDF：材质项字段偏移按版本取 `MATERIAL_FIELDS`。

## 三、验证

输入：`re_chunk_000.pak` 与 patch_001 解出的 `natives/stm`（2026-10-29）。

| 检查 | 输入 | 结果 |
|---|---|---|
| 解析与原样重建 | 全部 550 个 `.mmtr.260605155` | 全部解析，重建逐字节一致；PS 124291、VS 142783、CS 37325 等 |
| MDF 解析 | 原版 33 个与一个自制 mod 的 9 个 `.mdf2.51` | 全部解析，原样另存一致；179 个原版材质的 float 数字段全部与属性末端一致 |
| 新增参数与贴图 | `V_Chara_PL_Cloth_ALP` + 自制 mod 的 `ch001_00_00.mdf2.51`（材质 2） | `add_parameter` 16 字节（偏移 688）与 `add_texture`（`record_index` 105）各迁移 457 个 Shader，接口验证通过 |
| 批量扩展 | 同上，47 个 16 字节参数 + 15 个 Texture2D 一次提交 | 8 个分片 36 秒完成，同步 3 个材质，验证通过，导出后重开通过 |
| 回归 | `scripts/test_core.py` | 453 项通过，161 项因缺语料或 DXC 跳过 |

未新增合成测试；鬼武者的识别目前只由上述原版语料检查覆盖。

## 四、已知限制

- 标记行长度是进程内全局状态，同一会话交替打开不同游戏的模板时以最后打开的为准。
- `.mdf2.50`（原版 31 个）未登记。
- 结构编辑把新的贴图表与属性表追加到 MDF 文件末尾。RE-Mesh-Editor 按第一个材质的贴图表是否紧接材质项来区分鬼武者与 RE9 的 2.51 布局，因此会把这样的文件识别为 RE9；按原版顺序重排后可以正常导入。
- 计算阶段读取材质贴图的记录仍按现有规则拒绝 `add_texture`；`V_Chara_PL_Cloth_ALP` 选择 `record_index` 105（`ADirectDeferredPreTransformVfx`）可通过。

## 五、游戏内测试

2026-10-29。用扩展模板（`V_Chara_PL_Cloth_ALP` + 47 个参数 + 15 个 Texture2D）拼接 lilToon 计算、替换 72 个 GBuffer PS 的测试 mod 在游戏内加载，替换的 Shader 生效，新增参数与贴图按 MDF 中的值读取。
