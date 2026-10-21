import { computed, onUnmounted, ref, shallowRef, watch, type Ref } from "vue";
import { request, CoreRequestError } from "../api";
import {
  materialActions, terminalJob, type MaterialDraft, type MaterialJob, type MaterialPlan, type MaterialAction, type VariantOptions,
} from "../materialBatch";

/** 批量表单与后台短请求；轮询不重叠，网络重试保留同一请求键和原动作。 */
export function useMaterialBatch(options: {
  dll: Ref<string>;
  blocked: () => boolean;
  drain: () => Promise<unknown>;
  setBusy: (value: boolean) => void;
  committed: (plan: MaterialPlan) => Promise<void>;
  choosePath: (title: string, kind: "open" | "save", value?: string) => Promise<string | null>;
}) {
  const visible = shallowRef(false);
  const rows = ref<MaterialDraft[]>([]);
  const record = shallowRef<number | undefined>();
  const plan = shallowRef<MaterialPlan | null>(null);
  const job = shallowRef<MaterialJob | null>(null);
  const error = shallowRef("");
  const pending = shallowRef<Record<string, unknown> | null>(null);
  const loading = shallowRef(false);
  const mode = shallowRef<"shared_master" | "independent_variant">("shared_master");
  const variant = ref<VariantOptions>({ master: "MaterialShader/Variation/variant.mmtr", mmtr_path: "", mdf_path: "", overwrite: false });
  const notice = shallowRef("");
  const candidate = computed(() => !!job.value?.candidate_available);
  let planned: { actions: MaterialAction[]; record_index: number | undefined; dll: string; variant: VariantOptions | null } | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  let sequence = 0;
  let epoch = 0;
  const working = computed(() => loading.value || !!pending.value || candidate.value || (!!job.value && !terminalJob(job.value)));
  const hasTextures = computed(() => rows.value.some(row => row.action === "texture"));
  watch([rows, record, mode, variant, options.dll], () => { plan.value = null; planned = null; }, { deep: true });
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
    notice.value = "";
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
    notice.value = "";
    plan.value = null;
    loading.value = true;
    try {
      await options.drain();
      const args = { actions: materialActions(rows.value), record_index: record.value, dll: options.dll.value,
                     variant: mode.value === "independent_variant" ? { ...variant.value } : null };
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
      if (status.scope === "independent_variant") {
        notice.value = "变体候选已验证，请导出或丢弃候选。";
        return;
      }
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
        if (status.candidate_available) await request("material_variant_discard", { job_id: status.job_id });
        else if (!terminalJob(status)) await request("material_job_cancel", { job_id: status.job_id });
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
  async function exportVariant() {
    if (!candidate.value || !job.value || loading.value) return;
    loading.value = true;
    error.value = "";
    try {
      await request("material_variant_export", { job_id: job.value.job_id });
      job.value = await request<MaterialJob>("material_job_status", { job_id: job.value.job_id });
      plan.value = null;
      planned = null;
      notice.value = "独立变体已导出，原 MMTR/MDF 文档保持不变。";
    } catch (exc) {
      error.value = message(exc);
      job.value = await request<MaterialJob>("material_job_status", { job_id: job.value.job_id }).catch(() => job.value);
      if (!job.value?.candidate_available) { plan.value = null; planned = null; }
    } finally { loading.value = false; }
  }
  async function discardVariant() {
    if (!candidate.value || !job.value || loading.value) return;
    loading.value = true;
    try {
      job.value = await request<MaterialJob>("material_variant_discard", { job_id: job.value.job_id });
      notice.value = "变体候选已丢弃。";
      plan.value = null;
      planned = null;
    } catch (exc) { error.value = message(exc); }
    finally { loading.value = false; }
  }
  async function chooseTarget(key: "mmtr_path" | "mdf_path") {
    const path = await options.choosePath(key === "mmtr_path" ? "变体 MMTR 输出" : "变体 MDF 输出", "save", variant.value[key]);
    if (path) variant.value = { ...variant.value, [key]: path };
  }
  async function saveProject() {
    if (working.value) return;
    const path = await options.choosePath("保存材质工程", "save", "material.mmtrproj.json");
    if (!path) return;
    loading.value = true;
    error.value = "";
    try {
      await options.drain();
      await request("material_project_save", { path, actions: materialActions(rows.value), record_index: record.value,
        dll: options.dll.value, variant: mode.value === "independent_variant" ? { ...variant.value } : null });
      notice.value = "工程已保存，重开后需重新预览与验证。";
    } catch (exc) { error.value = message(exc); }
    finally { loading.value = false; }
  }
  async function readProject() {
    if (working.value) return;
    const path = await options.choosePath("打开材质工程", "open");
    if (!path) return;
    loading.value = true;
    error.value = "";
    try {
      await options.drain();
      const value = await request<{ changed_inputs: string[]; project: { request: {
        actions: MaterialAction[]; record_index: number | null; variant: VariantOptions | null;
      } } }>("material_project_read", { path, dll: options.dll.value });
      const intent = value.project.request;
      rows.value = intent.actions.map(action => ({ id: ++sequence, action: action.action, name: action.name,
        byteSize: action.action === "parameter" ? action.byte_size : 4,
        values: action.action === "parameter" ? action.values.join(", ") : "",
        path: action.action === "texture" ? action.path : "systems/rendering/NullBlack.tex" }));
      record.value = intent.record_index ?? undefined;
      mode.value = intent.variant ? "independent_variant" : "shared_master";
      if (intent.variant) variant.value = intent.variant;
      plan.value = null;
      planned = null;
      job.value = null;
      notice.value = value.changed_inputs.length ? "输入已改变：" + value.changed_inputs.join("、") + "，请重新预览。"
        : "工程意图已恢复，请重新预览。";
    } catch (exc) { error.value = message(exc); }
    finally { loading.value = false; }
  }
  onUnmounted(() => {
    disposed = true;
    if (timer) clearTimeout(timer);
    if (job.value?.candidate_available) {
      void request("material_variant_discard", { job_id: job.value.job_id }).catch(() => undefined);
    } else if (job.value && !terminalJob(job.value)) {
      void request("material_job_cancel", { job_id: job.value.job_id }).catch(() => undefined);
    }
  });
  return { visible, rows, record, plan, job, error, pending, loading, working, hasTextures,
           mode, variant, notice, candidate, add, open, close, preview, start, cancel, submitPending,
           exportVariant, discardVariant, chooseTarget, saveProject, readProject };
}
