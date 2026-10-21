import { computed, nextTick, onMounted, onUnmounted, shallowRef } from "vue";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { isDesktop, request } from "../api";
import { basename, type DocumentController, type DocumentState, type DocumentTab } from "../types";
import { droppedDocuments, useDocumentDrop } from "./useDocumentDrop";

/** 根容器串行管理标签生命周期；文档数据与后台轮询保留在各自的 Workbench 中。 */
export function useWorkspaces() {
  const tabs = shallowRef<DocumentTab[]>([]);
  const activeId = shallowRef("");
  const operating = shallowRef(false);
  const error = shallowRef("");
  const controllers = new Map<string, DocumentController>();
  let unlistenClose: (() => void) | undefined;
  let disposed = false;
  const active = computed(() => tabs.value.find(item => item.id === activeId.value));
  const blocked = () => operating.value || !!active.value?.modal;

  function register(id: string, controller: DocumentController) { controllers.set(id, controller); }
  function update(id: string, state: DocumentState) {
    tabs.value = tabs.value.map(item => item.id === id ? {
      ...item, ...state, label: state.source || state.mdfSource ? basename(state.source ?? state.mdfSource!) : "未打开文档",
    } : item);
  }
  function activate(id: string) {
    if (!blocked() && tabs.value.some(item => item.id === id)) activeId.value = id;
  }
  async function controller(id: string) {
    await nextTick();
    const value = controllers.get(id);
    if (!value) throw new Error("文档视图尚未就绪，请稍后重试。");
    if (!await value.ready) throw new Error("文档初始化失败，请刷新窗口后重试。");
    return value;
  }
  async function create() {
    const id = `document-${crypto.randomUUID()}`;
    await request("workspace_new", { document_id: id });
    tabs.value = [...tabs.value, { id, label: "未打开文档", source: null, mdfSource: null, dirty: false, busy: false, modal: false }];
    activeId.value = id;
    await controller(id);
    return id;
  }
  async function perform(work: () => Promise<void>) {
    if (blocked()) return;
    operating.value = true;
    error.value = "";
    try { await work(); }
    catch (exc) { error.value = exc instanceof Error ? exc.message : String(exc); }
    finally { operating.value = false; }
  }
  async function newTab() { await perform(async () => { await create(); }); }
  async function existing(path: string) {
    // 后端按规范路径与 samefile 检查，路径大小写、../ 和硬链接均定位同一所有者。
    return await request<{ document_id: string | null }>("workspace_find", { path });
  }
  async function openMmtr(path: string) {
    const found = await existing(path);
    if (found.document_id) { activeId.value = found.document_id; return found.document_id; }
    const empty = tabs.value.find(item => item.id === activeId.value && !item.source && !item.mdfSource && !item.dirty && !item.busy);
    const id = empty?.id ?? await create();
    activeId.value = id;
    if (!await (await controller(id)).loadMmtr(path)) throw new Error(`无法打开 ${basename(path)}，请查看该标签的错误信息。`);
    return id;
  }
  async function openMdf(path: string, preferredId = activeId.value, attach = true) {
    const found = await existing(path);
    if (found.document_id) { activeId.value = found.document_id; return; }
    const preferred = tabs.value.find(item => item.id === preferredId);
    const id = attach && preferred && !preferred.busy ? preferred.id : await create();
    activeId.value = id;
    const loaded = await (await controller(id)).loadMdf(path);
    if (loaded === undefined) throw new Error(`无法打开 ${basename(path)}，请查看该标签的错误信息。`);
  }
  async function openFiles(paths: string[]) {
    await perform(async () => {
      const files = droppedDocuments(paths);
      // 仅明确的一对文件自动关联；多对输入不猜测文件名对应关系。
      if (files.length === 2 && files.filter(item => item.kind === "mmtr").length === 1) {
        const id = await openMmtr(files.find(item => item.kind === "mmtr")!.path);
        await openMdf(files.find(item => item.kind === "mdf")!.path, id);
        return;
      }
      const failures: string[] = [];
      for (const file of files) {
        try {
          if (file.kind === "mmtr") await openMmtr(file.path);
          else await openMdf(file.path, activeId.value, files.length === 1 && !active.value?.mdfSource);
        } catch (exc) { failures.push(`${basename(file.path)}：${exc instanceof Error ? exc.message : String(exc)}`); }
      }
      if (failures.length) throw new Error(failures.join("；"));
    });
  }
  async function attachMdf(id: string, path: string) { await perform(() => openMdf(path, id)); }
  async function close(id: string) {
    await perform(async () => {
      if (!tabs.value.some(item => item.id === id)) return;
      activeId.value = id;
      const view = await controller(id);
      if (!await view.confirmClose()) return;
      await request("workspace_close", { document_id: id, discard: true });
      const index = tabs.value.findIndex(item => item.id === id);
      tabs.value = tabs.value.filter(item => item.id !== id);
      controllers.delete(id);
      activeId.value = tabs.value[Math.min(index, tabs.value.length - 1)]?.id ?? "";
      if (!tabs.value.length) await create();
    });
  }
  async function closeWindow() {
    await perform(async () => {
      // 先确认全部标签，再销毁窗口；中途取消不移除已经确认过的标签。
      for (const item of tabs.value) {
        activeId.value = item.id;
        if (!await (await controller(item.id)).confirmClose()) return;
      }
      await getCurrentWindow().destroy();
    });
  }
  function beforeUnload(event: BeforeUnloadEvent) {
    if (operating.value || tabs.value.some(item => item.dirty || item.busy)) {
      event.preventDefault(); event.returnValue = "";
    }
  }
  const fileDrop = useDocumentDrop({ blocked, open: openFiles, error: value => { error.value = value; } });
  async function initialize() {
    await perform(async () => {
      const result = await request<{ documents: Array<{ document_id: string; source: string | null; mdf_source: string | null; dirty: boolean; pending: boolean }> }>("workspace_list");
      tabs.value = result.documents.map(item => ({ id: item.document_id, source: item.source, mdfSource: item.mdf_source,
        label: basename(item.source ?? item.mdf_source ?? "未打开文档"), dirty: item.dirty, busy: item.pending, modal: false }));
      activeId.value = tabs.value[0]?.id ?? "";
      if (!tabs.value.length) await create();
    });
  }
  onMounted(async () => {
    window.addEventListener("beforeunload", beforeUnload);
    await initialize();
    if (isDesktop()) {
      const stop = await getCurrentWindow().onCloseRequested(event => { event.preventDefault(); void closeWindow(); });
      if (disposed) stop(); else unlistenClose = stop;
    }
  });
  onUnmounted(() => { disposed = true; unlistenClose?.(); window.removeEventListener("beforeunload", beforeUnload); });
  return { tabs, activeId, operating, error, fileDrop, register, update, activate, newTab, openFiles, attachMdf, close, initialize };
}
