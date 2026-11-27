# Resident Evil Requiem 支持

| 游戏 | MMTR 版本 | MDF 版本 | 是否完整测试 |
|---|---|---|---|
| Resident Evil Requiem | 251112994 | 2.51（108 字节材质项） | 否 |

[查看全部支持游戏](game-support.md)。

2026-11-27。结论：RE9 的 `.mmtr.251112994` 与 `.mdf2.51` 可以打开、原样另存、编辑 MDF，新增 UserMaterial 参数与 Texture2D，并对证明无作用的接口执行有界删除。游戏内效果未验证。

## 一、格式

| 项 | RE9 |
|---|---|
| MMTR 文件头 | per-target：4 组，各组一个 u16 程序数，Shader 区起点在 +0x10 |
| 记录长度 | 0x110 |
| 材质数据标记行 | 无（参数区从第 0 行开始，贴图索引表紧接参数区） |
| MDF 材质项 | 108 字节（flags 后多 8 字节，与鬼武者的 104 字节布局不同） |
| master 根目录 | `MaterialShader/`，部分母版在 `AppSystem/`、`VFX/`、`Gui/`、`systems/` 等目录 |

- 标记行：275 个有 MDF 引用的母版（各抽 12 个 PS）在 header=0 下没有越过「参数区 + 贴图/GPBF 索引表」的读取，181 个母版的贴图索引读取从参数区末端开始。8 个母版的 PS 中没有布局标记比较（`icmp` 模板 ID），所以不登记标记行。
- MDF 的游戏仍显示为未知：108 字节布局不能单凭字节区分 RE9、Pragmata 与新版 DD2，游戏身份以 MMTR 资源版本为准。
- 可见性缓冲与 meshlet 路径经 `Instances` 字节 52 读取网格表，形式与鬼武者相同，按网格表读取排除，不当作材质字段。
- 光追着色与 meshlet 计算经实例结构的几何字段（`BINDLESSINFOSRV.geometryByteOffset`、`Instances.geometry`）读取几何记录。这些行与材质数据共用 BindlessBuffer，但不是材质字段；只按反射命名的这两个字段识别，迁移不改写，覆盖检查不计为材质读取。
- 部分母版的 UserMaterial 含 MDF 不登记的 `CAPCOM_MATERIAL_RESERVE` 保留成员（例：`Env_Default_Emissive` 偏移 220）。

## 二、删除检查补全

删除前的覆盖分析要求每个 Shader 的材质读取都能被证明。RE9 原版在以下几处不完整，本轮补全：

| 形态 | 处理 |
|---|---|
| 浮点参数经大量运算传播，旧的 64 节点上限提前放弃 | 改为函数内完整前向闭包；只有超过 200000 个节点才判为未知 |
| 取值写入局部数组（`alloca`）或 groupshared / static 全局再读回 | 经同一内存基址的全部读取继续追踪；写入无法解析的指针仍判为未知 |
| `llvm.lifetime.start/end`（参数为 SSA 或对全局的常量 bitcast） | 只标记生命周期，不再视为未证明的调用；其它调用照旧拒绝 |
| meshlet VS/MS/CS 的网格表读取 | 按已证明形态计为已覆盖的消费者 |
| 几何记录及以其为下标的 heap ByteAddressBuffer | 按反射命名的几何字段识别，计为已覆盖 |
| 以 GPBF 槽为下标的 heap ByteAddressBuffer（风场等） | 读的是 MDF 引用的资源内容，计为已覆盖，该字段记为资源句柄 |
| 删除时的 `CAPCOM_MATERIAL_RESERVE` 保留成员 | 原位保留：偏移不变、仍在新参数区内、不与保留参数重叠且字段顺序不变；否则拒绝 |

## 三、验证

输入：Steam 版解包的 `natives/STM`（455 个 `.mmtr.251112994`，13633 个 `.mdf2.51`）；DXC 为 `dxcompiler.dll` 1.8.2505（Dawn 构建）。

| 检查 | 结果 |
|---|---|
| MMTR 解析与原样重建 | 455 个全部通过，逐字节一致 |
| MDF 解析与原样重建 | 13633 个全部识别为 108 字节材质项，逐字节一致 |
| MDF 字段与母版 UserMaterial 对照 | 母版在语料中的 36095 个材质项：33592 个参数名、偏移、宽度全部一致；2117 个母版 metadata 没有 UserMaterial（眼球类等）；386 个 MDF 比母版少成员（例：`Env_2Layer_Common_Dirt` 的 `PhysicalMaterialIndex2`），需先同步接口 |
| 新增参数与贴图 | `Env_Default_Emissive` + `ch0100_09_00_00.mdf2.51` 材质 6，一次新增 16 字节参数与一张 Texture2D（记录 7），615 个 Shader 迁移，导出后重开通过 |
| 迁移前后读取对照 | 615 个 Shader 全部符合预期：参数区读取不变，141 个 Shader 的 548 处贴图索引读取后移 16 字节 |
| 删除往返 | 对上述新增结果删除探针参数与贴图：预检 eligible，执行后导出重开，严格验证 615/615 通过；MDF 参数、贴图与 MMTR 成员表（含保留成员）与原版一致；615 个 Shader 的读取集合与原版逐一相同 |
| 关联布局严格验证（全部可绑定母版） | 214 个母版（各取一个可绑定的 MDF 材质，原版未修改）：212 个通过；共 95990 个 Shader，95988 个通过。补全前的样本母版 `Env_Default_Emissive` 为 422/615 |

可选语料回归：`tests/test_re9_support.py`。设置 `MMTR_TEST_RE9_NATIVES` 指向解包的 `natives` 运行解析、重建与 MDF 对照；再设置 `MMTR_DXC_PATH` 运行新增后删除的往返（约 8 分钟）。缺少时跳过。

## 四、已知限制

- `Env_HLOD_BendNormal` 与 `systems/effect/VFX_Default_Simple` 各有 1 个 Shader 读取 `CAPCOM_MATERIAL_RESERVE` 保留成员（偏移 8），删除布局不登记保留成员，所以这两个母版的删除与严格验证被拒绝。拒绝是安全的，不会写出错误结果。
- `Glass_RoughTransparent_Reaction` 的严格验证用时 359 秒，超过材质任务子进程默认的 300 秒上限，在工作台里会显示超时。放宽上限单独运行时通过。
- 光追与 meshlet 的几何识别只认 `geometry` 与 `geometryByteOffset` 两个反射字段名；其它游戏或其它命名不会被识别，按原规则处理。
- 新增与删除只对一组母版做了端到端执行；其余母版只跑了严格验证。

## 五、游戏内测试

尚未进行。

## 六、下一步

1. 用新增与删除的导出物做一次游戏内加载测试。
2. 删除布局纳入 `CAPCOM_MATERIAL_RESERVE`，并证明 MDF 重建后对应填充字节不变，使读取保留成员的母版也能删除。
