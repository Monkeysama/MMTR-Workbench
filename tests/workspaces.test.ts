import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createRenderer, defineComponent, h, nextTick, watch } from "vue";

// 使用真实 composables 与 Vue 生命周期，仅替换传输边界，不依赖浏览器或游戏文件。
registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith(".") && context.parentURL?.includes("/src/")) {
      const url = new URL(specifier + ".ts", context.parentURL);
      if (existsSync(fileURLToPath(url))) return { url: url.href, shortCircuit: true };
    }
    return next(specifier, context);
  },
  load(url, context, next) {
    if (url.endsWith("/src/api.ts")) return { format: "module", shortCircuit: true, source: `
      export class CoreRequestError extends Error {}
      export const isDesktop = () => false;
      export const request = (op, args = {}) => globalThis.workspaceRpc(op, args);
    ` };
    return next(url, context);
  },
});
const { useWorkspaces } = await import("../src/composables/useWorkspaces.ts");
const { useWorkbench } = await import("../src/composables/useWorkbench.ts");
const { droppedDocuments } = await import("../src/composables/useDocumentDrop.ts");
type View = ReturnType<typeof useWorkbench>;
type Workspace = ReturnType<typeof useWorkspaces>;
const settle = async () => { for (let i = 0; i < 8; i++) { await nextTick(); await new Promise(setImmediate); } };

/** 无 DOM 渲染器保留真实 keyed 组件，确保标签切换不被测试替身误当作重新挂载。 */
async function mount(recovered?: { revision: number; exported_revision: number; jobs: any[]; doc?: any }) {
  const listeners = new Map<string, EventListener>();
  const globals = globalThis as any;
  globals.window = { addEventListener: (name: string, fn: EventListener) => listeners.set(name, fn),
    removeEventListener: (name: string) => listeners.delete(name) };
  globals.localStorage = { getItem: () => null, setItem: () => {} };
  const contexts = new Map<string, { doc: any; mdf: any }>([["default", { doc: recovered?.doc ?? null, mdf: null }]]);
  const calls: Array<{ op: string; args: any }> = [];
  globals.workspaceRpc = async (op: string, args: any) => {
    calls.push({ op, args });
    if (op === "workspace_list") return { documents: [...contexts].map(([id, state]) => ({ document_id: id,
      source: state.doc?.source ?? null, mdf_source: state.mdf?.source ?? null, dirty: false, pending: false })) };
    if (op === "workspace_new") { contexts.set(args.document_id, { doc: null, mdf: null }); return {}; }
    if (op === "workspace_find") return { document_id: [...contexts].find(([, s]) => [s.doc?.source, s.mdf?.source].includes(args.path))?.[0] ?? null };
    if (op === "workspace_close") { contexts.delete(args.document_id); return { closed: true }; }
    const state = contexts.get(args.document_id);
    if (!state) throw new Error("文档已关闭");
    if (op === "ping") return { dxc: "test.dll" };
    if (op === "workspace_state") return { ...(recovered ?? { revision: 0, exported_revision: 0, jobs: [] }),
      doc: state.doc, mdf: state.mdf };
    if (op === "snapshot") return state.doc;
    if (op === "mdf_snapshot") return state.mdf;
    if (op === "open") {
      if (args.path.includes("bad")) throw new Error("格式错误");
      state.doc = { source: args.path, dirty: false, changes: { shaders: [], names: 0, references: 0, metadata: 0 },
        integrity: { name_hash_mismatches: [], shader_trailer_mismatches: [] }, shaders: [], metadata: [], passes: [] };
      return state.doc;
    }
    if (op === "mdf_open") {
      if (args.path.includes("bad")) throw new Error("MDF 格式错误");
      state.mdf = { source: args.path, unsaved: false }; return state.mdf;
    }
    if (op === "material_job_preview") return { digest: "test", source_revision: 0 };
    if (op === "material_job_submit") return { job_id: "task-" + args.document_id, state: "running" };
    if (op === "material_job_cancel") return { job_id: args.job_id, state: "cancelled" };
    throw new Error("未模拟的操作 " + op);
  };
  const views = new Map<string, View>();
  let workspace!: Workspace;
  const Child = defineComponent({ props: { id: { type: String, required: true } }, setup(props) {
    const wb = useWorkbench({ documentId: props.id, openMmtr: paths => { void workspace.openFiles(paths); },
      openMdf: path => { void workspace.attachMdf(props.id, path); } });
    views.set(props.id, wb);
    workspace.register(props.id, wb);
    watch(() => ({ source: wb.doc.value?.source ?? null, mdfSource: wb.mdfDoc.value?.source ?? null,
      dirty: wb.dirty.value, busy: wb.busy.value, modal: !!wb.dialog.value }), value => workspace.update(props.id, value), { immediate: true, flush: "sync" });
    return () => null;
  } });
  const Root = defineComponent({ setup() { workspace = useWorkspaces();
    return () => h("root", workspace.tabs.value.map(item => h(Child, { key: item.id, id: item.id }))); } });
  const renderer = createRenderer<any, any>({ createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {} });
  const app = renderer.createApp(Root);
  app.mount({});
  await settle();
  return { workspace, views, contexts, calls, listeners, dispose: () => app.unmount() };
}

test("多选分类去重且保持顺序，拒绝混入其他文件", () => {
  assert.deepEqual(droppedDocuments(["C:\\a.mmtr.240906211", "c:/a.MMTR.240906211", "C:/a.mdf2.45"]),
    [{ kind: "mmtr", path: "c:/a.MMTR.240906211" }, { kind: "mdf", path: "C:/a.mdf2.45" }]);
  assert.throws(() => droppedDocuments([]));
  assert.throws(() => droppedDocuments(["a.mmtr", "b.png"]));
});

test("多文档切换保留 HLSL、选择、编译结果与 MDF；重复打开定位原标签", async t => {
  const f = await mount(); t.after(f.dispose);
  const w = f.workspace;
  await w.openFiles(["a.mmtr.240906211"]);
  const a = w.activeId.value, view = f.views.get(a)!;
  view.source.source = "draft A"; view.selectedIndex.value = 3;
  view.compileResult.value = { ir: "compiled A" } as any;
  await w.attachMdf(a, "a.mdf2.45");
  await w.openFiles(["b.mmtr.240906211"]);
  const b = w.activeId.value;
  f.views.get(b)!.source.source = "draft B";
  w.activate(a);
  assert.equal(f.views.get(a), view);
  assert.equal(view.source.source, "draft A");
  assert.equal(view.selectedIndex.value, 3);
  assert.equal(view.compileResult.value?.ir, "compiled A");
  assert.equal(view.mdfDoc.value?.source, "a.mdf2.45");
  await w.openFiles(["a.mmtr.240906211"]);
  assert.equal(w.tabs.value.length, 2); assert.equal(w.activeId.value, a);
  assert.equal(f.views.get(b)!.source.source, "draft B");
  const event = new Event("beforeunload", { cancelable: true });
  f.listeners.get("beforeunload")!(event);
  assert.equal(event.defaultPrevented, true);
});

test("关闭非活动脏标签需确认，取消保留状态，最后一个关闭后创建新身份", async t => {
  const f = await mount(); t.after(f.dispose);
  const w = f.workspace, first = w.activeId.value;
  f.views.get(first)!.source.source = "keep";
  await w.newTab(); const other = w.activeId.value;
  const closing = w.close(first); await settle();
  assert.equal(w.activeId.value, first);
  assert.equal(f.views.get(first)!.dialog.value?.kind, "confirm");
  f.views.get(first)!.finishDialog(null); await closing;
  assert.equal(w.tabs.value.length, 2);
  const confirmed = w.close(first); await settle();
  f.views.get(first)!.finishDialog(""); await confirmed;
  assert.equal(w.activeId.value, other);
  await w.close(other);
  assert.equal(w.tabs.value.length, 1);
  assert.notEqual(w.activeId.value, first); assert.notEqual(w.activeId.value, other);
});

test("明确一对自动关联；多对不猜配，单个解析失败不覆盖已打开文档", async t => {
  const f = await mount(); t.after(f.dispose);
  const w = f.workspace;
  await w.openFiles(["a.mdf2.45", "a.mmtr.240906211"]);
  const a = w.activeId.value;
  assert.equal(f.views.get(a)!.mdfDoc.value?.source, "a.mdf2.45");
  await w.openFiles(["bad.mmtr.240906211", "b.mmtr.240906211", "b.mdf2.45", "c.mdf2.45"]);
  assert.match(w.error.value, /bad/);
  assert.equal(f.views.get(a)!.doc.value?.source, "a.mmtr.240906211");
  const b = [...f.views.values()].find(v => v.doc.value?.source === "b.mmtr.240906211")!;
  assert.equal(b.mdfDoc.value, null);
  assert.equal([...f.contexts.values()].filter(s => s.mdf).length, 3);
});

test("MDF 失败汇总并保留旧文档，取消替换不误报错误", async t => {
  const f = await mount(); t.after(f.dispose);
  const w = f.workspace;
  await w.openFiles(["a.mmtr.240906211", "a.mdf2.45"]);
  const id = w.activeId.value, view = f.views.get(id)!;
  await w.attachMdf(id, "bad.mdf2.45");
  assert.match(w.error.value, /bad\.mdf2\.45/);
  assert.equal(view.mdfDoc.value?.source, "a.mdf2.45");
  view.mdfDoc.value = { ...view.mdfDoc.value!, unsaved: true };
  const replacing = w.attachMdf(id, "b.mdf2.45"); await settle();
  assert.equal(view.dialog.value?.kind, "confirm");
  view.finishDialog(null); await replacing;
  assert.equal(w.error.value, "");
  assert.equal(view.mdfDoc.value?.source, "a.mdf2.45");
  assert.equal(view.mdfDoc.value?.unsaved, true);
  assert.ok(!f.calls.some(call => call.op === "mdf_open" && call.args.path === "b.mdf2.45"));
});

test("后台任务固定发送到所属标签，切换后关闭可取消且不影响另一个文档", async t => {
  const f = await mount(); t.after(f.dispose);
  const w = f.workspace;
  await w.openFiles(["a.mmtr.240906211"]); const a = w.activeId.value, view = f.views.get(a)!;
  view.materialBatch.rows.value = [{ id: 1, action: "parameter", name: "Extra", byteSize: 4, values: "0", path: "" }];
  await view.materialBatch.preview(); await view.materialBatch.start();
  await w.openFiles(["b.mmtr.240906211"]); const b = w.activeId.value;
  const request = f.calls.find(call => call.op === "material_job_submit")!;
  assert.equal(request.args.document_id, a);
  assert.equal(view.materialBatch.working.value, true);
  const closing = w.close(a); await settle();
  assert.equal(view.dialog.value?.kind, "confirm");
  view.finishDialog(""); await closing; await settle();
  assert.equal(f.views.get(b)!.doc.value?.source, "b.mmtr.240906211");
  assert.ok(f.calls.some(call => call.op === "material_job_cancel" && call.args.document_id === a));
});

test("刷新恢复已导出状态及仍运行的任务，关闭前继续提示并发送所属取消请求", async t => {
  const f = await mount({ revision: 8, exported_revision: 8,
    doc: { source: "a.mmtr.240906211", dirty: true },
    jobs: [{ job_id: "existing", state: "running", operation: "extend" }] });
  t.after(f.dispose);
  const view = f.views.get("default")!;
  assert.equal(view.unsaved.value, false);
  assert.equal(view.pendingWork.value, true);
  assert.equal(view.materialBatch.job.value?.job_id, "existing");
  const closing = f.workspace.close("default"); await settle();
  assert.equal(view.dialog.value?.kind, "confirm");
  view.finishDialog(""); await closing; await settle();
  assert.ok(f.calls.some(call => call.op === "material_job_cancel" && call.args.job_id === "existing"));
});

test("刷新恢复未导出 revision 后仍显示脏标签", async t => {
  const f = await mount({ revision: 9, exported_revision: 8, jobs: [],
    doc: { source: "a.mmtr.240906211", dirty: true } });
  t.after(f.dispose);
  assert.equal(f.views.get("default")!.unsaved.value, true);
  assert.equal(f.workspace.tabs.value[0].dirty, true);
});

test("验证区分未执行与关联覆盖，MDF 或 DXC 变化立即撤销旧报告", async t => {
  const f = await mount(); t.after(f.dispose);
  await f.workspace.openFiles(["a.mmtr.240906211"]);
  const view = f.views.get(f.workspace.activeId.value)!;
  const globals = globalThis as any, original = globals.workspaceRpc;
  let report = { status: "skipped", scope: "metadata_delta", valid: null,
    checked_shaders: 0, checked_records: 0, metadata_edits: 0, issues: [] };
  const requests: any[] = [];
  globals.workspaceRpc = async (op: string, args: any) => {
    if (op === "mmtr_validate_interfaces") { requests.push(args); return report; }
    return original(op, args);
  };
  await view.validateMmtrInterfaces();
  assert.equal(view.interfaceValidation.value?.state, "skipped");
  assert.match(view.status.value, /未执行.*0 个 Shader/);
  assert.equal(requests[0].scope, "metadata_delta");
  report = { status: "passed", scope: "associated_layout", valid: true,
    checked_shaders: 3, checked_records: 15, checked_markers: 3, metadata_edits: 0, issues: [] } as any;
  await view.validateMmtrInterfaces("associated_layout");
  assert.equal(view.interfaceValidation.value?.state, "passed");
  assert.equal(view.interfaceValidation.value?.checkedMarkers, 3);
  assert.match(view.status.value, /关联布局验证通过.*3 个 Shader、15 条记录/);
  view.mdfDoc.value = { source: "changed.mdf2.45" } as any;
  assert.equal(view.interfaceValidation.value, null);
  report = { ...report, status: "failed", valid: false,
    issues: [{ kind: "associated_layout", problem: "旧标记" }] } as any;
  await view.validateMmtrInterfaces("associated_layout");
  assert.equal(view.interfaceValidation.value?.state, "failed");
  assert.match(view.status.value, /关联布局验证失败/);
  view.dll.value = "other.dll";
  assert.equal(view.interfaceValidation.value, null);
  let finish!: (value: typeof report) => void;
  const delayed = new Promise<typeof report>(resolve => { finish = resolve; });
  globals.workspaceRpc = async (op: string, args: any) =>
    op === "mmtr_validate_interfaces" ? delayed : original(op, args);
  const pending = view.validateMmtrInterfaces("associated_layout");
  view.dll.value = "new.dll";
  finish(report);
  await pending;
  assert.equal(view.interfaceValidation.value, null);
  assert.match(view.status.value, /输入已变化.*已丢弃/);
});
