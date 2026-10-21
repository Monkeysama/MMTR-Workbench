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
}
export interface MaterialJob {
  job_id: string;
  state: "queued" | "running" | "cancelling" | "succeeded" | "failed" | "cancelled" | "stale";
  stage: string;
  completed: number;
  total: number;
  elapsed_seconds: number;
  error: string | null;
}
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
