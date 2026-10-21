import { computed, onMounted, onUnmounted, reactive, shallowRef } from "vue";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { request, isDesktop } from "../api";
import { droppedDocuments, useDocumentDrop } from "./useDocumentDrop";
import { serialEdits } from "../utils/serialEdits";
import { useMaterialBatch } from "./useMaterialBatch";
import {
  basename,
  type CompileResult,
  type DialogState,
  type DocumentInfo,
  type Metadata,
  type MdfDocument,
  type MdfSetting,
  type SourceInput,
  type ReconstructionResult,
  type ReconstructionTrace,
  type Tab,
} from "../types";

export function useWorkbench() {
  const doc = shallowRef<DocumentInfo | null>(null);
  const mdfDoc = shallowRef<MdfDocument | null>(null);
  const mdfSaving = shallowRef(false);
  const mdfEdits = serialEdits();
  const tab = shallowRef<Tab>("overview");
  const busy = shallowRef(false);
  const status = shallowRef("准备就绪");
  const error = shallowRef("");
  const selectedIndex = shallowRef<number | null>(null);
  const ir = shallowRef("");
  const expert = shallowRef(false);
  const dll = shallowRef(localStorage.getItem("mmtr-dxc") ?? "");
  const source = reactive<SourceInput>({
    source:
      "// 自备 HLSL 源代码；DXIL IR 不是原始 HLSL。\n// 编译后需保持资源、常量布局和输入输出兼容。\n",
    entry: "main",
    profile: "ps_6_2",
  });
  const compileResult = shallowRef<CompileResult | null>(null);
  const sourceTrace = shallowRef<ReconstructionTrace | null>(null);
  const compiledFingerprint = shallowRef("");
  const revision = shallowRef(0);
  const exportedRevision = shallowRef(0);
  const unsaved = computed(() => !!doc.value?.dirty && revision.value !== exportedRevision.value);
  const mdfUnsaved = computed(() => !!mdfDoc.value?.unsaved);
  const dialog = shallowRef<DialogState | null>(null);
  let resolveDialog: ((value: string | null) => void) | null = null;
  let unlistenClose: (() => void) | undefined;
  const shader = computed(
    () =>
      doc.value?.shaders.find((s) => s.index === selectedIndex.value) ?? null,
  );
  const changeCount = computed(() =>
    doc.value
      ? doc.value.changes.shaders.length +
        doc.value.changes.names +
        doc.value.changes.references +
        doc.value.changes.metadata
      : 0,
  );
  const integrityOk = computed(
    () =>
      !!doc.value &&
      !doc.value.integrity.name_hash_mismatches.length &&
      !doc.value.integrity.shader_trailer_mismatches.length,
  );
  // 批量任务复用核心事务，成功后只刷新已提交文档并推进一次未保存计数。
  const materialBatch = useMaterialBatch({
    dll,
    blocked: () => busy.value || !!dialog.value || !doc.value,
    drain: () => mdfEdits.drain(),
    setBusy: value => { busy.value = value; },
    committed: async () => {
      const [mmtr, mdf] = await Promise.all([
        request<DocumentInfo>("snapshot"), request<MdfDocument | null>("mdf_snapshot"),
      ]);
      doc.value = mmtr;
      mdfDoc.value = mdf;
      revision.value += 1;
      ir.value = "";
      sourceTrace.value = null;
      status.value = "批次已完成 · MMTR 与全部已知关联材质已同步，请分别另存";
    },
  });

  function ask(options: DialogState) {
    if (dialog.value) return Promise.resolve(null);
    dialog.value = options;
    return new Promise<string | null>((resolve) => {
      resolveDialog = resolve;
    });
  }
  function finishDialog(value: string | null) {
    dialog.value = null;
    resolveDialog?.(value);
    resolveDialog = null;
  }
  async function path(title: string, kind: "open" | "save", value = "") {
    return ask({
      title,
      kind,
      value,
      description:
        kind === "open"
          ? "选择本地文件，或输入完整路径。"
          : "选择输出路径，导出为独立文件。",
      confirmLabel: kind === "open" ? "打开" : "保存",
      placeholder: "输入完整文件路径",
    });
  }
  async function discard(scope: "mmtr" | "mdf" | "all" = "all") {
    await mdfEdits.drain();
    const pending = (scope !== "mdf" && unsaved.value) || (scope !== "mmtr" && mdfUnsaved.value);
    if (!pending) return true;
    return (
      (await ask({
        title: "放弃当前修改？",
        description: "当前文档中仍有编辑。请先另存为文件，或放弃后继续。",
        value: "",
        kind: "confirm",
        confirmLabel: "放弃并继续",
      })) !== null
    );
  }
  async function run<T>(
    label: string,
    work: () => Promise<T>,
  ): Promise<T | undefined> {
    if (busy.value) return;
    busy.value = true;
    error.value = "";
    status.value = label;
    try {
      await mdfEdits.drain();
      return await work();
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
      status.value = "操作未完成";
    } finally {
      busy.value = false;
    }
  }
  async function openFile(given?: string) {
    if (busy.value || !(await discard("mmtr"))) return;
    const p =
      given ?? (await path("打开 MMTR", "open", doc.value?.source ?? ""));
    if (!p) return;
    return loadMmtr(p);
  }
  async function loadMmtr(p: string) {
    return run("解析 MMTR 并验证 hash…", async () => {
      doc.value = await request<DocumentInfo>("open", { path: p });
      mdfDoc.value = await request<MdfDocument | null>("mdf_snapshot");
      revision.value = 0;
      exportedRevision.value = 0;
      selectedIndex.value = null;
      sourceTrace.value = null;
      ir.value = "";
      tab.value = "overview";
      status.value = `已打开 ${basename(p)}`;
      return true;
    });
  }
  async function openMdf(given?: string) {
    if (busy.value || !(await discard("mdf"))) return;
    const p = given ?? (await path("打开 MDF", "open", mdfDoc.value?.source ?? ""));
    if (!p) return;
    return loadMdf(p);
  }
  async function loadMdf(p: string) {
    return run("解析 MDF 2.45…", async () => {
      mdfDoc.value = await request<MdfDocument>("mdf_open", { path: p, material_index: 0 });
      tab.value = "mdf";
      status.value = `已打开 ${basename(p)}`;
      return true;
    });
  }
  async function openDroppedFiles(paths: string[]) {
    if (busy.value || dialog.value) return;
    const files = droppedDocuments(paths);
    const scope = files.mmtr && files.mdf ? "all" : files.mmtr ? "mmtr" : "mdf";
    if (!(await discard(scope))) return;
    if (files.mmtr && !(await loadMmtr(files.mmtr))) return;
    if (files.mdf) await loadMdf(files.mdf);
  }
  const fileDrop = useDocumentDrop({
    blocked: () => busy.value || !!dialog.value,
    open: openDroppedFiles,
    error: message => { error.value = message; },
  });
  async function selectMdfMaterial(index: number) {
    if (!mdfDoc.value || busy.value) return;
    await run("读取材质绑定…", async () => {
      mdfDoc.value = await request<MdfDocument>("mdf_select", { material_index: index });
      status.value = `已选择 MDF 材质 #${index}`;
    });
  }
  async function linkMdf() {
    if (!mdfDoc.value || !doc.value || busy.value) return;
    await run("检查 MDF ↔ MMTR 绑定…", async () => {
      mdfDoc.value = await request<MdfDocument>("mdf_link", {
        mmtr_path: doc.value?.source,
      });
      status.value = "MDF ↔ MMTR 绑定检查完成";
    });
  }
  async function autoMdfEdit(op: string, args: Record<string, unknown>, label: string) {
    const selected = mdfDoc.value?.selected;
    if (!selected || busy.value) return;
    mdfSaving.value = true;
    try {
      await mdfEdits.enqueue(async () => {
        try {
          mdfDoc.value = await request<MdfDocument>(op, { material_index: selected.index, ...args });
          error.value = "";
          status.value = `${label}已自动暂存 · 另存 MDF 后写入文件`;
        } catch (e) {
          error.value = e instanceof Error ? e.message : String(e);
          status.value = `${label}暂存失败，请修正后重试`;
        }
      });
    } finally { mdfSaving.value = mdfEdits.pending > 0; }
  }
  async function editMdfProperty(name: string, components: Record<number, number>) {
    await autoMdfEdit("mdf_set_property_components", { name, components }, `参数 ${name}`);
  }
  async function editMdfTexture(slot: number, value: string) {
    await autoMdfEdit("mdf_set_texture", { slot, path: value }, "纹理路径");
  }
  async function addMdfProperty() {
    if (!mdfDoc.value?.selected || busy.value) return;
    if (doc.value && mdfDoc.value.selected.mmtr_linked === false) { error.value = "当前材质未使用已打开的 MMTR，不能从它新增接口。"; return; }
    const available = mdfDoc.value.binding?.missing_mdf_properties ?? [];
    if (!available.length) { error.value = "当前关联 MMTR 没有可新增的参数。"; return; }
    const firstSize = available[0].mmtr_size;
    const input = await ask({ title: "新增 MDF 参数", description: "选择当前 MMTR 中尚未写入该材质的参数，并填写初始 Float 值。", value: "", kind: "mdf-property", confirmLabel: "新增", fields: { name: available[0].name, values: Array(firstSize % 4 === 0 ? firstSize / 4 : 1).fill("0").join(",") }, selectOptions: available.map(row => ({ value: row.name, label: `${row.name} · ${row.mmtr_size} B`, size: row.mmtr_size })) });
    if (input === null) return;
    let fields: { name: string; values: string };
    try { fields = JSON.parse(input); } catch { error.value = "参数输入格式无效。"; return; }
    const name = fields.name.trim(), raw = fields.values;
    const values = raw.split(",").map(x => Number(x.trim()));
    if (!values.length || values.some(x => !Number.isFinite(x))) { error.value = "参数值必须是逗号分隔的有限数字。"; return; }
    if (!name) { error.value = "参数名称不能为空。"; return; }
    await autoMdfEdit("mdf_add_property", { name, values }, `参数 ${name}`);
  }
  async function deleteMdfProperty(name: string) {
    if (!mdfDoc.value?.selected || busy.value) return;
    const approved = await ask({ title: "删除 MDF 参数？", description: `将删除参数 ${name}，导出后 MMTR 绑定报告会标记缺失。`, value: "", kind: "confirm", confirmLabel: "删除" });
    if (approved === null) return;
    await autoMdfEdit("mdf_delete_property", { name }, `删除参数 ${name}`);
  }
  async function addMdfTexture() {
    if (!mdfDoc.value?.selected || busy.value) return;
    if (doc.value && mdfDoc.value.selected.mmtr_linked === false) { error.value = "当前材质未使用已打开的 MMTR，不能从它新增接口。"; return; }
    const available = mdfDoc.value.binding?.missing_mdf_textures ?? [];
    if (!available.length) { error.value = "当前关联 MMTR 没有可新增的纹理槽。"; return; }
    const input = await ask({ title: "新增 MDF 纹理槽", description: "选择当前 MMTR 中尚未写入该材质的 SRV，并填写纹理资源路径。", value: "", kind: "mdf-texture", confirmLabel: "新增", fields: { name: available[0].name, path: "" }, selectOptions: available.map(row => ({ value: row.name, label: row.name })) });
    if (input === null) return;
    let fields: { name: string; path: string };
    try { fields = JSON.parse(input); } catch { error.value = "纹理输入格式无效。"; return; }
    const name = fields.name.trim(), pathValue = fields.path.trim();
    if (!name || !pathValue) { error.value = "纹理槽名称和路径不能为空。"; return; }
    await autoMdfEdit("mdf_add_texture", { name, path: pathValue }, `纹理槽 ${name}`);
  }
  async function deleteMdfTexture(slot: number) {
    if (!mdfDoc.value?.selected || busy.value) return;
    const texture = mdfDoc.value.selected.textures[slot];
    const approved = await ask({ title: "删除 MDF 纹理槽？", description: `将删除纹理槽 ${texture?.name ?? slot}，导出后 MMTR 绑定报告会标记缺失。`, value: "", kind: "confirm", confirmLabel: "删除" });
    if (approved === null) return;
    await autoMdfEdit("mdf_delete_texture", { slot }, `删除纹理槽 ${texture?.name ?? slot}`);
  }
  async function editMdfSetting(field: MdfSetting, value: number) {
    const selected = mdfDoc.value?.selected;
    if (!selected || busy.value) return;
    const isFlags = field === "flags_a" || field === "flags_b";
    await run("更新 MDF 材质设置…", async () => {
      mdfDoc.value = await request<MdfDocument>(isFlags ? "mdf_set_flags" : "mdf_set_setting", {
        material_index: selected.index,
        ...(isFlags ? { [field]: value } : { field, value }),
      });
      status.value = "材质设置已暂存 · 另存 MDF 后写入文件";
    });
  }
  async function exportMdf() {
    if (!mdfDoc.value || busy.value) return;
    const initial = mdfDoc.value.source.replace(/(\.mdf2\.45)$/i, "_edited$1");
    const p = await path("另存为 MDF", "save", initial);
    if (p)
      await run("导出 MDF 并重新校验…", async () => {
        const report = await request<{ size: number; sha256: string }>("mdf_export", { path: p });
        mdfDoc.value = await request<MdfDocument>("mdf_snapshot");
        status.value = `MDF 导出完成 · ${p}`;
        void report;
      });
  }
  async function resetMdf() {
    if (!mdfDoc.value || busy.value) return;
    await run("恢复 MDF 原始值…", async () => {
      mdfDoc.value = await request<MdfDocument>("mdf_reset");
      status.value = "MDF 修改已撤销";
    });
  }
  async function analyzeMigration() {
    if (!mdfDoc.value?.selected || busy.value) return;
    const sourcePath = await path("选择所选材质的原 MMTR", "open", doc.value?.source ?? "");
    if (!sourcePath) return;
    const targetPath = await path("选择迁移目标 MMTR", "open");
    if (!targetPath) return;
    await run("比较源表面与目标模板…", async () => {
      mdfDoc.value = await request<MdfDocument>("mdf_migration", { source_path: sourcePath, target_path: targetPath });
      status.value = "迁移差异已生成 · 尚未改写材质或 Shader";
    });
  }
  async function exportMdfReport() {
    if (!mdfDoc.value || busy.value) return;
    const p = await path("导出 MDF 绑定与迁移报告", "save", "mdf_binding_migration.json");
    if (!p) return;
    await run("导出材质报告…", async () => {
      await request("mdf_report", { path: p });
      status.value = `已导出 ${p}`;
    });
  }
  async function mutate(
    op: string,
    args: Record<string, unknown>,
    label: string,
  ) {
    await run(label, async () => {
      doc.value = await request<DocumentInfo>(op, args);
      mdfDoc.value = await request<MdfDocument | null>("mdf_snapshot");
      revision.value++;
      status.value = "修改已暂存 · 另存导出后写入文件";
    });
  }
  function selectShader(index: number) {
    selectedIndex.value = index;
    ir.value = "";
    tab.value = "shaders";
  }
  async function disassemble() {
    if (!shader.value) return;
    const index = shader.value.index;
    await run("正在反汇编 DXIL…", async () => {
      const result = await request<{ ir: string }>("disassemble", {
        index,
        dll: dll.value,
      });
      if (index === selectedIndex.value) ir.value = result.ir;
      status.value = `Shader #${index} 反汇编完成`;
    });
  }
  async function reconstruct() {
    if (!shader.value || busy.value) return;
    if (source.source !== "// 自备 HLSL 源代码；DXIL IR 不是原始 HLSL。\n// 编译后需保持资源、常量布局和输入输出兼容。\n") {
      const approved = await ask({
        title: "用重建代码替换编辑区？",
        description: "HLSL 编辑区已有内容，请先复制保存需要保留的代码。",
        value: "", kind: "confirm", confirmLabel: "替换编辑区",
      });
      if (approved === null) return;
    }
    const index = shader.value.index;
    await run("正在检查可重建指令并生成 HLSL…", async () => {
      const result = await request<ReconstructionResult>("reconstruct", { index, dll: dll.value });
      Object.assign(source, { source: result.source, entry: result.entry, profile: result.profile });
      sourceTrace.value = result.origin_ir && result.source_map &&
        result.origin_shader_index !== undefined && result.origin_shader_sha256 && result.origin_document
        ? { baseline: { source: result.source, entry: result.entry, profile: result.profile },
            ir: result.origin_ir, sourceMap: result.source_map, shaderIndex: result.origin_shader_index,
            shaderSha256: result.origin_shader_sha256, documentSource: result.origin_document }
        : null;
      compileResult.value = null;
      compiledFingerprint.value = "";
      tab.value = "source";
      status.value = `Shader #${index} 已重建 · 请编译并校验后应用`;
    });
  }
  async function exportShader() {
    if (!shader.value || busy.value) return;
    const p = await path(
      "导出 DXBC",
      "save",
      `shader_${shader.value.index.toString().padStart(4, "0")}.dxbc`,
    );
    if (p)
      await run("导出 DXBC…", async () => {
        await request("export_shader", { path: p, index: selectedIndex.value });
        status.value = `已导出 ${p}`;
      });
  }
  async function replaceShader() {
    if (!shader.value || busy.value) return;
    const p = await path("导入替换 DXBC", "open");
    if (p) {
      await mutate(
        "replace",
        {
          path: p,
          index: selectedIndex.value,
          dll: dll.value,
          expert: expert.value,
        },
        "检查 DXBC 接口和反射…",
      );
      ir.value = "";
    }
  }
  async function restoreShader() {
    if (shader.value) {
      await mutate(
        "restore_shader",
        { index: selectedIndex.value },
        "恢复原始着色器…",
      );
      ir.value = "";
    }
  }
  async function rename(row: Metadata) {
    const name = await ask({
      title: "修改元数据名称",
      description: `最多 ${row.capacity} 个 ASCII 字节。共享引用会同步改名；不会修改 HLSL 反射和 MDF 供值。`,
      value: row.name,
      kind: "text",
      confirmLabel: "暂存改名",
    });
    if (name !== null)
      await mutate("rename", { pointer: row.pointer, name }, "更新共享名称…");
  }
  function addMmtrParameter() {
    materialBatch.open("parameter");
  }
  async function deleteMmtrParameter(row: Metadata) {
    if (!doc.value || busy.value) return;
    const approved = await ask({ title: "删除 MMTR 参数？", description: `将从 UserMaterial metadata 删除 ${row.name}。Shader 字节码必须同步删除对应成员。`, value: "", kind: "confirm", confirmLabel: "删除" });
    if (approved === null) return;
    await run("删除 MMTR UserMaterial metadata…", async () => {
      await request("mmtr_delete_cbuffer_member", { cbuffer: "UserMaterial", name: row.name });
      const refreshed = await request<DocumentInfo>("snapshot");
      doc.value = refreshed;
      status.value = "MMTR 参数 metadata 已暂存 · 请确认 Shader 字节码接口后另存 MMTR";
    });
  }
  async function addMmtrResource(kind: "srvs" | "samplers" | "uavs") {
    if (!doc.value || busy.value) return;
    if (kind === "srvs") { materialBatch.open("texture"); return; }
    const label = kind === "samplers" ? "Sampler" : "UAV";
    const input = await ask({ title: `新增 MMTR ${label}`, description: `记录必须已经有非空 ${label} 表，名称必须已由 Shader 字节码声明。`, value: "", kind: "resource", confirmLabel: "新增", fields: { record: "", name: "" } });
    if (input === null) return;
    let fields: { record: string; name: string };
    try { fields = JSON.parse(input); } catch { error.value = "资源输入格式无效。"; return; }
    const name = fields.name.trim(), recordIndex = Number(fields.record);
    if (!Number.isInteger(recordIndex) || recordIndex < 0 || !name) { error.value = "记录索引必须是非负整数，资源名称不能为空。"; return; }
    await run(`更新 MMTR ${label} metadata…`, async () => {
      await request("mmtr_add_resource", { kind, record_index: recordIndex, name, auto_sync: false, dll: dll.value });
      doc.value = await request<DocumentInfo>("snapshot");
      status.value = `MMTR ${label} metadata 已暂存 · 仍需同步绑定映射及 Shader`;
    });
  }
  async function deleteMmtrResource(kind: "srvs" | "samplers" | "uavs") {
    if (!doc.value || busy.value) return;
    const label = kind === "srvs" ? "SRV" : kind === "samplers" ? "Sampler" : "UAV";
    const input = await ask({ title: `删除 MMTR ${label}`, description: `共享资源表会影响使用同一表的记录，Shader 字节码必须同步删除。`, value: "", kind: "resource", confirmLabel: "删除", fields: { record: "", name: "" } });
    if (input === null) return;
    let fields: { record: string; name: string };
    try { fields = JSON.parse(input); } catch { error.value = "资源输入格式无效。"; return; }
    const recordText = fields.record, name = fields.name.trim();
    const recordIndex = Number(recordText);
    if (!Number.isInteger(recordIndex) || recordIndex < 0 || !name) { error.value = "记录索引必须是非负整数，资源名称不能为空。"; return; }
    await run("删除 MMTR SRV metadata…", async () => {
      await request("mmtr_delete_resource", { kind, record_index: recordIndex, name });
      doc.value = await request<DocumentInfo>("snapshot");
      status.value = `MMTR ${label} metadata 已暂存 · 请确认 Shader 字节码接口后另存 MMTR`;
    });
  }
  // 通过后端 DXC 反射核对当前 metadata 与所有关联 Shader；验证失败不写入文件。
  async function validateMmtrInterfaces() {
    if (!doc.value || busy.value) return;
    await run("用 DXC 反射验证 MMTR 接口…", async () => {
      const report = await request<{
        valid: boolean;
        checked_shaders: number;
        metadata_edits: number;
        issues: Array<{ kind: string; shader_index?: number; record_index?: number; name?: string; problem?: string }>;
      }>("mmtr_validate_interfaces", { dll: dll.value });
      if (report.metadata_edits === 0) {
        status.value = "当前没有待验证的 MMTR metadata 修改";
        return;
      }
      if (!report.valid) {
        const details = report.issues
          .slice(0, 4)
          .map((issue) => issue.problem ?? `${issue.kind} ${issue.name ?? ""}`)
          .join("；");
        throw new Error(`Shader 接口验证失败：${details}${report.issues.length > 4 ? "等" : ""}`);
      }
      status.value = `Shader 接口验证通过 · 已检查 ${report.checked_shaders} 个 DXIL`;
    });
  }
  async function redirect(record: number, field: number) {
    const text = await ask({
      title: "重定向阶段",
      description:
        "输入目标着色器索引。阶段和 Shader Model 必须相同，VS 两个别名同步更新。",
      value: "",
      kind: "text",
      confirmLabel: "检查并重定向",
    });
    if (text === null) return;
    const target = Number(text);
    if (!text.trim() || !Number.isInteger(target) || target < 0) {
      error.value = "请输入有效的非负整数索引。";
      return;
    }
    await mutate(
      "redirect",
      { record, field, target, dll: dll.value, expert: expert.value },
      "检查目标接口与资源布局…",
    );
  }
  async function exportMmtr() {
    if (!doc.value || busy.value) return;
    const initial = doc.value.source.replace(/(\.mm[tr]{2}.*)$/i, "_edited$1");
    const p = await path("另存为 MMTR", "save", initial);
    if (p)
      await run("重建引用、长度和尾项，重新解析输出…", async () => {
        const report = await request<{
          unchanged: boolean;
          size: number;
          sha256: string;
        }>("export", { path: p, dll: dll.value });
      exportedRevision.value = revision.value;
        status.value = report.unchanged
          ? "导出完成 · 与原文件逐字节相同"
          : `导出完成 · 结构复查通过 · ${p}`;
      });
  }
  async function exportData(op: "report" | "export_layout") {
    const p = await path(
      op === "report" ? "导出修改清单" : "导出参数布局",
      "save",
      op === "report" ? "mmtr_changes.json" : "material_layout.csv",
    );
    if (p)
      await run("导出文件…", async () => {
        await request(op, { path: p });
        status.value = `已导出 ${p}`;
      });
  }
  async function reset() {
    if (await discard("mmtr")) {
      await mutate("reset", {}, "恢复原始文档…");
      ir.value = "";
    }
  }
  async function compile() {
    compileResult.value = null;
    const input = { ...source };
    await run("Microsoft DXC 编译 HLSL…", async () => {
      compileResult.value = await request<CompileResult>("compile", {
        ...input,
        dll: dll.value,
      });
      compiledFingerprint.value = JSON.stringify(input);
      status.value = "HLSL 编译成功";
    });
  }
  function freshCompiled() {
    if (
      !compileResult.value ||
      compiledFingerprint.value !== JSON.stringify(source)
    ) {
      error.value = "源码或选项已变化，请先重新编译。";
      return false;
    }
    return true;
  }
  async function applyCompiled() {
    if (!freshCompiled()) return;
    if (!shader.value) {
      error.value = "请先在着色器页选择一个目标块。";
      return;
    }
    await mutate(
      "apply_compiled",
      { index: selectedIndex.value, dll: dll.value, expert: expert.value },
      "检查编译结果与原接口…",
    );
    ir.value = "";
  }
  // 显式同步编译结果的接口变化，并把可推断 metadata 变更纳入同一份文档状态。
  async function syncCompiledInterface() {
    if (!freshCompiled()) return;
    if (!shader.value) {
      error.value = "请先在着色器页选择一个目标块。";
      return;
    }
    await run("同步 DXIL 接口与 MMTR metadata…", async () => {
      const result = await request<DocumentInfo & {
        interface_sync: {
          shader_index: number;
          metadata_operations: Array<Record<string, unknown>>;
          affected_records: number[];
          affected_shader_indices: number[];
          requires_other_shader_variants: boolean;
        };
      }>("mmtr_sync_compiled_interface", {
        index: selectedIndex.value,
        dll: dll.value,
      });
      doc.value = result;
      const sync = result.interface_sync;
      status.value = sync.requires_other_shader_variants
        ? `接口已应用 · 受影响 Shader：${sync.affected_shader_indices.join(", ")}；仍需同步其他变体`
        : "DXIL 接口与 MMTR metadata 已同步";
      ir.value = "";
    });
  }
  // 收集当前编译结果及其它变体的 DXBC 文件，在后端以单一事务校验并同步。
  async function syncCompiledInterfaces() {
    if (!freshCompiled()) return;
    if (!shader.value) {
      error.value = "请先在着色器页选择一个目标块。";
      return;
    }
    const text = await ask({
      title: "批量同步 Shader 变体",
      description: "当前编译结果用于选中 Shader；其它变体按“索引=DXBC完整路径”填写，多个项目用分号分隔。留空会先检查缺少的变体并保持文档不变。",
      value: "",
      kind: "text",
      confirmLabel: "检查并同步",
      placeholder: "例如 5=C:\\work\\shader5.dxbc;6=C:\\work\\shader6.dxbc",
    });
    if (text === null) return;
    const items: Array<{ index: number; use_compiled?: boolean; path?: string }> = [
      { index: selectedIndex.value!, use_compiled: true },
    ];
    const seen = new Set([selectedIndex.value!]);
    for (const token of text.split(/[;\n]+/).map((part) => part.trim()).filter(Boolean)) {
      const match = token.match(/^(\d+)\s*=\s*(.+)$/);
      if (!match) {
        error.value = `无法解析批量项“${token}”，格式应为索引=DXBC路径。`;
        return;
      }
      const index = Number(match[1]);
      if (seen.has(index)) {
        error.value = `Shader #${index} 重复指定。`;
        return;
      }
      seen.add(index);
      items.push({ index, path: match[2].trim() });
    }
    await run("批量校验并同步 DXIL 接口…", async () => {
      const result = await request<DocumentInfo & {
        interface_sync: {
          complete: boolean;
          applied: boolean;
          affected_shader_indices: number[];
          missing_shader_indices: number[];
          metadata_operations: Array<Record<string, unknown>>;
        };
      }>("mmtr_sync_compiled_interfaces", { items, dll: dll.value });
      doc.value = result;
      const sync = result.interface_sync;
      if (!sync.complete) {
        status.value = `仍缺少受影响 Shader：${sync.missing_shader_indices.join(", ")}`;
      } else {
        status.value = `批量接口同步完成 · 已应用 ${sync.affected_shader_indices.length} 个 Shader`;
      }
      ir.value = "";
    });
  }
  async function exportCompiled() {
    if (!freshCompiled()) return;
    const p = await path("导出编译后的 DXBC", "save", "compiled.dxbc");
    if (p)
      await run("导出编译结果…", async () => {
        await request("export_compiled", { path: p });
        status.value = `已导出 ${p}`;
      });
  }
  function useProfile() {
    if (shader.value)
      source.profile =
        shader.value.stage + "_" + shader.value.model.replace(".", "_");
  }
  function saveDll(value: string) {
    dll.value = value;
    localStorage.setItem("mmtr-dxc", value);
  }
  function beforeUnload(e: BeforeUnloadEvent) {
    if (unsaved.value || mdfUnsaved.value || busy.value || mdfSaving.value) {
      e.preventDefault();
      e.returnValue = "";
    }
  }
  onMounted(async () => {
    window.addEventListener("beforeunload", beforeUnload);
    try {
      const ping = await request<{ dxc: string }>("ping");
      if (!dll.value) dll.value = ping.dxc;
      const existing = await request<DocumentInfo | null>("snapshot");
      mdfDoc.value = await request<MdfDocument | null>("mdf_snapshot");
      if (existing) {
        doc.value = existing;
        revision.value = existing.dirty ? 1 : 0;
        status.value = "已恢复内存中的文档";
      }
    } catch (e) {
      error.value = String(e);
    }
    if (isDesktop()) {
      unlistenClose = await getCurrentWindow().onCloseRequested(
        async (event) => {
          event.preventDefault();
          if (!busy.value && (await discard()))
            await getCurrentWindow().destroy();
        },
      );
    }
  });
  onUnmounted(() => {
    window.removeEventListener("beforeunload", beforeUnload);
    unlistenClose?.();
  });
  return {
    fileDrop,
    materialBatch,
    doc,
    mdfDoc,
    mdfSaving,
    tab,
    busy,
    status,
    error,
    selectedIndex,
    shader,
    ir,
    expert,
    dll,
    source,
    compileResult,
    sourceTrace,
    dialog,
    changeCount,
    unsaved,
    mdfUnsaved,
    integrityOk,
    finishDialog,
    openFile,
    openMdf,
    selectMdfMaterial,
    linkMdf,
    editMdfProperty,
    editMdfTexture,
    addMdfProperty,
    deleteMdfProperty,
    addMdfTexture,
    deleteMdfTexture,
    editMdfSetting,
    exportMdf,
    resetMdf,
    analyzeMigration,
    exportMdfReport,
    selectShader,
    disassemble,
    reconstruct,
    exportShader,
    replaceShader,
    restoreShader,
    rename,
    addMmtrParameter,
    deleteMmtrParameter,
    addMmtrResource,
    deleteMmtrResource,
    validateMmtrInterfaces,
    redirect,
    exportMmtr,
    exportData,
    reset,
    compile,
    applyCompiled,
    syncCompiledInterface,
    syncCompiledInterfaces,
    exportCompiled,
    useProfile,
    saveDll,
  };
}
