import { computed, onMounted, onUnmounted, shallowRef } from "vue";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { isDesktop } from "../api";

export function droppedDocuments(paths: string[]) {
  const files: { mmtr?: string; mdf?: string } = {};
  const unique = new Map(paths.map(path => [path.replaceAll("\\", "/").toLowerCase(), path]));
  if (!unique.size) throw new Error("请拖入 MMTR 或 MDF 文件。");
  for (const path of unique.values()) {
    const kind = /\.(mmtr|mmrt)(\.\d+)?$/i.test(path) ? "mmtr"
      : /\.mdf2?(\.\d+)?$/i.test(path) ? "mdf" : null;
    if (!kind) throw new Error("只支持拖入 MMTR 或 MDF 文件（含版本后缀），请勿拖入文件夹或其他文件。");
    if (files[kind]) throw new Error("一次最多打开一个 MMTR 和一个 MDF，请分别拖入同类文件。");
    files[kind] = path;
  }
  return files;
}

export function useDocumentDrop(options: {
  blocked: () => boolean;
  open: (paths: string[]) => Promise<void>;
  error: (message: string) => void;
}) {
  const active = shallowRef(false);
  const paths = shallowRef<string[]>([]);
  const opening = shallowRef(false);
  let disposed = false;
  let unlisten: (() => void) | undefined;
  const problem = computed(() => {
    if (options.blocked() || opening.value) return "请先完成当前操作，再拖入文件。";
    if (!isDesktop()) return "拖入本地文件请使用桌面版。";
    try { droppedDocuments(paths.value); return ""; }
    catch (e) { return e instanceof Error ? e.message : String(e); }
  });
  const title = computed(() => problem.value ? "暂时无法打开" : "松开以打开文件");
  const description = computed(() => problem.value || "MMTR · MDF，可同时拖入各一个");

  async function drop(incoming: string[]) {
    active.value = false;
    if (options.blocked() || opening.value) return;
    opening.value = true;
    try { await options.open(incoming); }
    catch (e) { options.error(e instanceof Error ? e.message : String(e)); }
    finally { opening.value = false; }
  }
  // Prevent browser navigation for file drops. Native desktop paths arrive
  // through Tauri; browser File objects deliberately do not expose full paths.
  function preventFileNavigation(event: DragEvent) {
    if (event.dataTransfer?.types.includes("Files")) {
      event.preventDefault();
      if (event.type === "drop" && !isDesktop()) options.error("拖入本地文件请使用桌面版。");
    }
  }
  onMounted(async () => {
    window.addEventListener("dragover", preventFileNavigation);
    window.addEventListener("drop", preventFileNavigation);
    if (!isDesktop()) return;
    try {
      const stop = await getCurrentWebview().onDragDropEvent(({ payload }) => {
        if (disposed) return;
        if (payload.type === "enter") {
          paths.value = payload.paths;
          active.value = true;
        } else if (payload.type === "leave") active.value = false;
        else if (payload.type === "drop") void drop(payload.paths);
      });
      if (disposed) stop();
      else unlisten = stop;
    } catch (e) {
      if (!disposed) options.error(`拖入文件监听失败：${e instanceof Error ? e.message : String(e)}`);
    }
  });
  onUnmounted(() => {
    disposed = true;
    unlisten?.();
    window.removeEventListener("dragover", preventFileNavigation);
    window.removeEventListener("drop", preventFileNavigation);
  });
  return { active, title, description, problem };
}
