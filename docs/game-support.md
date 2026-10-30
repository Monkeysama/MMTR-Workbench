# 多游戏支持

2026-10-31。目标是用同一套事务编辑核心支持更多 RE Engine 游戏的 MMTR/MDF；格式依据是 REasy 的 profile 与本地公开文件清单，不把“能解析”当成“游戏内有效”。

## 当前状态

| 游戏 | MMTR | MDF | 当前能力 | 证据 |
|---|---|---|---|---|
| MH Wilds | 240906211，0x110，4/5 组，uniform，有缓存键 | 2.45 | MMTR/MDF 编辑 | 原有支持；真实语料与游戏内验证 |
| RE4 | 221007879，0x108，3 组，uniform，有缓存键 | 2.32 | MMTR/MDF 编辑 | 原有支持 |
| 鬼武者 WotS / Demo | 260605155 / 251112814，0x110，per-target，4 组，16 字节材质头 | 2.51，104 字节材质项 | MMTR/MDF 编辑、参数/纹理新增、Shader 替换 | PR 作者报告原版 550 个 MMTR、33 个 MDF 和游戏内测试通过；本机只跑合成测试，未复现 |
| MHRISE | 220427553，0x108，3 组，uniform，无缓存键 | 2.23 | MMTR/MDF 编辑 | 用户提供的 3 个真实 MMTR 与提取 MDF 完成解析、重存、增删；无游戏内验证 |
| RE8 | 2102188797，0x108，5 组，uniform，无缓存键 | 2.19 | MMTR/MDF 编辑 | 合成测试；无本机原版语料 |
| RE2RT | 2109101635，0x108，5 组，uniform，无缓存键 | 2.21 | MMTR/MDF 编辑 | 合成测试；无本机原版语料 |
| RE2 | 1808160001，0x108，4 组，uniform，无缓存键 | 2.10 | MMTR/MDF 编辑 | 合成测试；无本机原版语料 |
| RE3 | 1905100741，0x108，4 组，uniform，无缓存键 | 2.13 | MMTR/MDF 编辑 | 合成测试；无本机原版语料 |
| SF6 | 221102761 / 250109895，0x108，3 组，uniform，有缓存键 | 2.31 | MMTR/MDF 编辑 | 合成测试；无本机原版语料 |
| DD2 旧版 | 240112003 / 240423143，0x108，3 组，uniform，有缓存键 | 未登记 | 仅 MMTR | 合成测试；MDF 未开放 |
| RE9 / Pragmata / DD2 当前版 | 251112994 / 251112995 / 260511270，0x110，per-target，4 组 | 2.51 未按这些游戏单独验证 | 仅 MMTR | 合成测试；MDF 2.51 目前按鬼武者布局登记 |

## 依据

- `D:\Project\REasy\file_handlers\sdf\re_engine_profiles.py`：0x108 与 0x110 只在 compute 线程组偏移（0x100/0x108）和拓扑偏移（0x106/0x10E）不同；0x110 的 per-target 文件头是一组 u16 计数后对齐 8 字节。
- `D:\Project\REasy\file_handlers\mmtr\profiles\re_engine_profiles.py`：SF6 两个资源版本、DD2 旧版两个资源版本、3 个 target、`runtime_cache_keys=True`。
- `D:\Project\REasy\resources\data\lists\SF6_STM.list`：12,781 个 `.mdf2.31` 路径，889 个路径含 `mastermaterial`；其中 `product/mastermaterial/character` 450 个 MMTR、`environments` 94 个、`postmaterial` 40 个。示例：`natives/stm/develop/lookdev/substance/defaultshader.mmtr.221102761`。
- DD2 当前清单含 `.mdf2.51`；本机没有 SF6/DD2 的可用原版文件，因此只能做合成数据测试。
- `RE8_STM.list` 含 6,128 个 `.mdf2.19`、308 个 `.mmtr.2102188797`；`MHR_STM.list` 含 8,782 个 `.mdf2.23`、340 个 `.mmtr.220427553`；`RE2_RT_STM.list` 含 2,802 个 `.mdf2.21`、119 个 `.mmtr.2109101635`。这些版本的 MMTR 不写 8 字节运行时缓存键，Shader 记录首尾相接。
- `RE2_STM.list` 含 2,794 个 `.mdf2.10`、114 个 `.mmtr.1808160001`；`RE3_STM.list` 含 2,642 个 `.mdf2.13`、112 个 `.mmtr.1905100741`。

## 验证边界

- `tests/test_tier_d_support.py` 验证五个 0x110 per-target 版本、4 个 target 的不等计数、头填充必须为零、混合零计数拒绝、改名/元数据编辑后的 Shader 指针迁移、结构分组的 target 归属。
- `tests/test_sf6_dd2_support.py` 验证资源版本选择、3 组约束、0x108 记录长度、SF6 MDF 2.31 的 master 根目录、未登记 MDF 版本的拒绝。
- `tests/test_re8_mhrise_support.py` 验证 RE8/MHRISE/RE2RT 的 0x108 无缓存键解析、Shader 首尾相接、组数约束、改名重建和结构报告不虚构状态缓存键。
- `tests/test_re2_re3_support.py` 验证 RE2/RE3 的 0x108 四组、无缓存键解析、改名重建与组数拒绝。
- `tests/test_old_mdf_support.py` 用合成 MDF 验证 2.10/2.13/2.19/2.21/2.23 的材质项、属性头、纹理头（24/32 字节）、master 根目录、参数/纹理编辑、结构性增删，以及旧版本没有第二组 flags、bake 或 shader LOD 时拒绝写入。
- `tests/test_onimusha_support.py` 用合成数据验证 24 字节 MMTR 头、改名重建、MDF 2.51 参数/纹理/母版增删和 `float_count` 更新。
- 合并后 `npm run test:core` 为 485 项通过、72 项因缺语料跳过；跳过项不是通过。除 MH Wilds、MHRISE 和 PR 作者报告的鬼武者结果外，其余游戏没有真实语料或游戏内证据。
- MDF 布局目前按版本选择：`.mdf2.51` 统一使用鬼武者的 104 字节材质项布局，RE9/Pragmata/DD2 当前版没有单独验证。接入这些游戏的真实 MDF 前，不应把 2.51 的打开结果当作已确认支持。
- 新增 Texture2D 的现有计算阶段限制仍适用；RE4 的 `PreTransform*VFXCS` 读取材质纹理时会被拒绝，SF6/DD2/鬼武者不能据此推定所有 Shader 都可用。MMTR 安全删除仍受描述符使用链分析范围限制，不能把 metadata-only 删除当作安全删除。

## 接入下一款游戏的步骤

1. 从 REasy profile 和原版文件确认：资源版本、记录长度、组数、文件头布局、MDF 版本与 master 根目录。
2. MMTR 只需在 `inspect_mmtr.FORMATS` 登记记录长度、组数和是否带 8 字节运行时缓存键；若文件头或 Shader 排布不同，先补 `inspect_data` 的定位逻辑，不要用猜字段的方式打开。
3. MDF 先在 `mmtr_editor.mdf.MDF_VERSIONS` 登记；材质项/属性头/纹理头尺寸不同则单独实现版本分支。
4. 跑合成测试，再用原版语料跑解析、重存和编辑；没有语料时只报告“识别路径已加”，不报告“游戏已支持”。
