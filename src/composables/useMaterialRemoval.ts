import { computed, onUnmounted, shallowRef, watch } from "vue";
import { request as coreRequest, CoreRequestError } from "../api";
import type { MaterialJob } from "../materialBatch";

export type RemovalAction = { action: "parameter" | "texture"; name: string };
type RemovalReport = {
  eligible: boolean;
  coverage: { deletion_supported: boolean; scope: string };
  failures: Array<{ shader_index: number; error: string }>;
  shaders_total?: number;
};

/** 通过共享后台任务执行删除预检与候选提交；轮询不重叠，提交使用全新的幂等键。 */
export function useMaterialRemoval(options: {
  setBusy: (value: boolean) => void;
  committed: () => Promise<void>;
  drain: () => Promise<unknown>;
  dll: () => string;
  documentId: string;
}) {
  const request = <T>(op: string, args: Record<string, unknown> = {}) =>
    coreRequest<T>(op, { ...args, document_id: options.documentId });
  const visible = shallowRef(false);
  const action = shallowRef<RemovalAction | null>(null);
  const job = shallowRef<MaterialJob | null>(null);
  const report = shallowRef<RemovalReport | null>(null);
  const loading = shallowRef(false);
  const pending = shallowRef<{ op: string; args: Record<string, unknown> } | null>(null);
  const working = computed(() => loading.value || !!pending.value || !!job.value && !["succeeded", "failed", "cancelled", "stale"].includes(job.value.state));
  watch(working, options.setBusy, { flush: "sync" });
  const error = shallowRef("");
  const stage = computed(() => job.value?.stage ?? "");
  let timer: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  let epoch = 0;

  function open(next: RemovalAction) {
    if (working.value) return;
    action.value = { ...next };
    job.value = null;
    report.value = null;
    error.value = "";
    visible.value = true;
  }
  function close() {
    if (working.value) return;
    visible.value = false;
  }
  function message(exc: unknown) { return exc instanceof Error ? exc.message : String(exc); }
  function schedule(delay = 500) {
    if (timer) clearTimeout(timer);
    if (!disposed) timer = setTimeout(poll, delay);
  }
  async function accept(status: MaterialJob, generation: number) {
    if (disposed || generation !== epoch) return;
    job.value = status;
    if (!["succeeded", "failed", "cancelled", "stale"].includes(status.state)) { schedule(); return; }
    if (status.state !== "succeeded") {
      error.value = status.error ?? "删除任务未完成。";
      return;
    }
    if (status.operation === "removal_preview") {
      const result = await request<{ result: RemovalReport }>("material_job_result", { job_id: status.job_id });
      if (disposed || generation !== epoch) return;
      report.value = result.result;
      if (!result.result.eligible) {
        error.value = "当前 Shader 消费者或分析覆盖不满足删除条件。";
      }
      return;
    }
    loading.value = true;
    try { await options.committed(); } finally { loading.value = false; }
    visible.value = false;
  }
  async function poll() {
    if (disposed || !job.value) return;
    const generation = epoch;
    try { await accept(await request<MaterialJob>("material_job_status", { job_id: job.value.job_id }), generation); }
    catch (exc) { if (!disposed && generation === epoch) { error.value = "查询暂时失败，正在重试：" + message(exc); schedule(1200); } }
  }
  function restore(status: MaterialJob) {
    // 刷新后可查询或取消已有任务；删除动作须重新从当前接口选择，不复用缺失表单。
    job.value = status;
    schedule();
  }
  async function submit(kind: "preview" | "remove") {
    if (!action.value || working.value) return;
    pending.value = { op: kind === "preview" ? "material_removal_preview" : "material_removal_submit", args: {
      actions: [{ ...action.value }], request_key: crypto.randomUUID(), dll: options.dll(),
      ...(kind === "remove" ? { expected_plan: job.value?.plan_digest } : {}),
    } };
    await retry();
  }
  async function retry() {
    if (!pending.value || loading.value) return;
    loading.value = true;
    error.value = "";
    const generation = ++epoch;
    try {
      await options.drain();
      const next = await request<MaterialJob>(pending.value.op, pending.value.args);
      job.value = next;
      pending.value = null;
      await accept(next, generation);
    } catch (exc) {
      if (exc instanceof CoreRequestError) pending.value = null;
      error.value = message(exc);
      if (!pending.value && job.value && !disposed) schedule(1200);
    } finally { loading.value = false; }
  }
  async function cancel() {
    if (!job.value || ["succeeded", "failed", "cancelled", "stale"].includes(job.value.state)) return;
    epoch += 1;
    try { await accept(await request<MaterialJob>("material_job_cancel", { job_id: job.value.job_id }), epoch); }
    catch (exc) { error.value = message(exc); schedule(); }
  }
  onUnmounted(() => {
    disposed = true;
    if (timer) clearTimeout(timer);
    if (job.value && !["succeeded", "failed", "cancelled", "stale"].includes(job.value.state))
      void request("material_job_cancel", { job_id: job.value.job_id }).catch(() => undefined);
  });
  return { visible, action, job, report, loading, working, pending, error, stage, open, close,
    preview: () => submit("preview"), remove: () => submit("remove"), cancel, retry, restore };
}
