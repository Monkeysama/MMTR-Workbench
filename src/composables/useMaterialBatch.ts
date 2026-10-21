import { computed, onUnmounted, ref, shallowRef, watch, type Ref } from "vue";
import { request, CoreRequestError } from "../api";
import {
  materialActions, terminalJob, type MaterialDraft, type MaterialJob, type MaterialPlan, type MaterialAction,
} from "../materialBatch";

/** 批量表单与后台短请求；轮询不重叠，网络重试保留同一请求键和原动作。 */
export function useMaterialBatch(options: {
  dll: Ref<string>;
  blocked: () => boolean;
  drain: () => Promise<unknown>;
  setBusy: (value: boolean) => void;
  committed: (plan: MaterialPlan) => Promise<void>;
}) {
  const visible = shallowRef(false);
  const rows = ref<MaterialDraft[]>([]);
  const record = shallowRef<number | undefined>();
  const plan = shallowRef<MaterialPlan | null>(null);
  const job = shallowRef<MaterialJob | null>(null);
  const error = shallowRef("");
  const pending = shallowRef<Record<string, unknown> | null>(null);
  const loading = shallowRef(false);
  let planned: { actions: MaterialAction[]; record_index: number | undefined; dll: string } | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  let sequence = 0;
  let epoch = 0;
  const working = computed(() => loading.value || !!pending.value || (!!job.value && !terminalJob(job.value)));
  const hasTextures = computed(() => rows.value.some(row => row.action === "texture"));
  watch([rows, record, options.dll], () => { plan.value = null; planned = null; }, { deep: true });
  watch(working, value => options.setBusy(value), { flush: "sync" });

  function add(action: "parameter" | "texture") {
    rows.value.push({ id: ++sequence, action, name: "", byteSize: 4, values: "0",
                      path: "systems/rendering/NullBlack.tex" });
  }
  function open(action: "parameter" | "texture" = "parameter") {
    if (options.blocked() || working.value) return;
    rows.value = [];
    record.value = undefined;
    plan.value = null;
    job.value = null;
    error.value = "";
    pending.value = null;
    add(action);
    visible.value = true;
  }
  function close() {
    if (!working.value) visible.value = false;
  }
  function message(exc: unknown) { return exc instanceof Error ? exc.message : String(exc); }
  function schedule(delay = 600) {
    if (timer) clearTimeout(timer);
    if (!disposed) timer = setTimeout(poll, delay);
  }
  async function preview() {
    if (working.value) return;
    error.value = "";
    plan.value = null;
    loading.value = true;
    try {
      await options.drain();
      const args = { actions: materialActions(rows.value), record_index: record.value, dll: options.dll.value };
      if (hasTextures.value && (!Number.isInteger(record.value) || record.value! < 0)) throw new Error("请选择 PS 绘制记录。");
      const result = await request<MaterialPlan>("material_job_preview", args);
      planned = args;
      plan.value = result;
    } catch (exc) { error.value = message(exc); }
    finally { loading.value = false; }
  }
  async function accept(status: MaterialJob) {
    if (disposed) return;
    job.value = status;
    if (!terminalJob(status)) {
      schedule();
      return;
    }
    if (status.state === "succeeded") {
      loading.value = true;
      try {
        const result = await request<{ result: { plan: MaterialPlan } }>("material_job_result", { job_id: status.job_id });
        await options.committed(result.result.plan);
      } catch (exc) { error.value = "批次已提交，刷新未完成：" + message(exc); }
      finally { loading.value = false; }
    } else {
      error.value = status.error ?? (status.state === "cancelled" ? "批次已取消。" : "批次未完成。");
    }
    plan.value = null;
    planned = null;
  }
  async function poll() {
    if (disposed || !job.value || terminalJob(job.value)) return;
    const generation = epoch;
    try {
      const status = await request<MaterialJob>("material_job_status", { job_id: job.value.job_id });
      if (generation !== epoch) return;
      error.value = "";
      await accept(status);
    } catch (exc) {
      if (!disposed && generation === epoch) {
        error.value = "查询暂时失败，正在重试：" + message(exc);
        schedule(1200);
      }
    }
  }
  async function submitPending() {
    if (!pending.value || loading.value) return;
    loading.value = true;
    error.value = "";
    try {
      const status = await request<MaterialJob>("material_job_submit", pending.value);
      if (disposed) {
        if (!terminalJob(status)) await request("material_job_cancel", { job_id: status.job_id });
        return;
      }
      // 先保存任务身份再释放 pending，避免出现允许编辑的中间状态。
      job.value = status;
      pending.value = null;
      await accept(status);
    } catch (exc) {
      if (exc instanceof CoreRequestError) {
        pending.value = null;
        plan.value = null;
        planned = null;
        error.value = message(exc);
      } else error.value = "提交结果尚未确认，请重试原请求：" + message(exc);
    }
    finally { loading.value = false; }
  }
  async function start() {
    if (working.value || !plan.value || !planned) return;
    pending.value = { ...planned, expected_plan: plan.value.digest, request_key: crypto.randomUUID() };
    await submitPending();
  }
  async function cancel() {
    if (!job.value || terminalJob(job.value)) return;
    epoch += 1;
    try {
      if (timer) clearTimeout(timer);
      await accept(await request<MaterialJob>("material_job_cancel", { job_id: job.value.job_id }));
    } catch (exc) {
      error.value = message(exc);
      schedule();
    }
  }
  onUnmounted(() => {
    disposed = true;
    if (timer) clearTimeout(timer);
    if (job.value && !terminalJob(job.value)) {
      void request("material_job_cancel", { job_id: job.value.job_id }).catch(() => undefined);
    }
  });
  return { visible, rows, record, plan, job, error, pending, loading, working, hasTextures,
           add, open, close, preview, start, cancel, submitPending };
}
