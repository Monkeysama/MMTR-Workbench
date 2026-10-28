/** 桌面批量追加的协议类型；偏移和关联范围以核心返回的计划为准。 */
export type MaterialAction =
  | { action: "parameter"; name: string; byte_size: 4 | 8 | 12 | 16; values: number[] }
  | { action: "texture"; name: string; path: string };
export interface MaterialDraft {
  id: number;
  action: "parameter" | "texture";
  name: string;
  byteSize: 4 | 8 | 12 | 16;
  values: string;
  path: string;
}
export interface MaterialPlan {
  digest: string;
  source_revision: number;
  old: { parameter_bytes: number; texture_names: string[] };
  new: { parameter_bytes: number; texture_names: string[] };
  target_materials: number[];
  parameters: Array<{ name: string; byte_size: number; byte_offset: number; values: number[] }>;
  textures: Array<{ name: string; path: string }>;
  shader_candidates: number[];
  scope?: "shared_master" | "independent_variant";
  selected_material?: number;
  known_users?: Array<[number, string, string]>;
  external_users?: "not_scanned";
  variant?: { mmtr: { path: string }; mdf: { path: string } };
}
export interface VariantOptions {
  master: string;
  mmtr_path: string;
  mdf_path: string;
  overwrite: boolean;
}
export interface MaterialJob {
  job_id: string;
  plan_digest: string;
  state: "queued" | "running" | "cancelling" | "succeeded" | "failed" | "cancelled" | "stale";
  operation?: string;
  stage: string;
  completed: number;
  total: number;
  elapsed_seconds: number;
  error: string | null;
  scope?: "shared_master" | "independent_variant";
  candidate_available?: boolean;
  exported?: boolean;
}

export const materialJobStageLabels: Record<string, string> = {
  queued: "等待执行", preparing: "准备候选", starting: "启动 DXC", planning: "核对计划",
  shaders: "处理 Shader", shards: "处理 Shader 分片", building: "重建 MMTR", validating: "验证接口",
  validated: "候选已验证", committing: "核对源状态", complete: "修改已暂存",
  candidate_ready: "候选已验证", exporting: "正在导出", exported: "变体已导出", discarded: "候选已丢弃",
  analyzing_removal: "分析接口使用情况", removing_shaders: "处理 Shader 删除",
  validating_removal: "验证删除候选", source_changed: "源状态已改变",
};
/** 各游戏独立变体的默认 master；与核心 MDF_VERSIONS 的根目录一致，未知游戏按荒野处理。 */
const variantMasters: Record<string, string> = {
  MHWilds: "MaterialShader/Variation/variant.mmtr",
  RE4: "_Chainsaw/MasterMaterial/Variation/variant.mmtr",
};
export const defaultVariantMaster = (game?: string) => variantMasters[game ?? ""] ?? variantMasters.MHWilds;
/** 用户未改过的默认 master 随当前 MDF 的游戏切换；已手动填写的值保持不变。 */
export const variantMasterFor = (current: string, game?: string) =>
  Object.values(variantMasters).includes(current) ? defaultVariantMaster(game) : current;

export const terminalJob = (job: MaterialJob) =>
  ["succeeded", "failed", "cancelled", "stale"].includes(job.state);

/** 保留动作顺序；不默默修正默认值数量，避免表单输入与预览不一致。 */
export function materialActions(rows: MaterialDraft[]): MaterialAction[] {
  if (!rows.length || rows.length > 100) throw new Error("每批需要 1–100 项。");
  return rows.map(row => {
    const name = row.name.trim();
    if (!/^[A-Za-z_]\w*$/.test(name)) throw new Error("名称必须是 ASCII 标识符。");
    if (row.action === "texture") {
      if (!row.path.trim()) throw new Error("贴图路径不能为空。");
      return { action: "texture", name, path: row.path.trim() };
    }
    const values = row.values.trim().split(/[\s,，]+/).map(Number);
    if (!row.values.trim() || values.length * 4 !== row.byteSize || values.some(value => !Number.isFinite(value))) {
      throw new Error(name + " 的默认值数量必须与 float 宽度一致。");
    }
    return { action: "parameter", name, byte_size: row.byteSize, values };
  });
}
