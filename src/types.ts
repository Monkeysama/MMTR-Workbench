export interface Shader {
  index: number;
  stage: string;
  model: string;
  size: number;
  currentSize: number;
  offset: number;
  changed: boolean;
  passes: string[];
}
export interface StageRef {
  field: number;
  stage: string;
  shader: number;
}
export interface Pass {
  index: number;
  group: number;
  name: string;
  refs: StageRef[];
}
export interface Metadata {
  kind: string;
  buffer: string;
  name: string;
  pointer: number;
  capacity: number;
  offset: number;
  byteOffset: number | null;
  size: number | null;
}
// 当前输入的接口检查结果；MMTR、MDF 或 DXC 改变后必须失效。
export interface InterfaceValidation {
  state: "passed" | "failed" | "skipped";
  scope: "metadata_delta" | "associated_layout";
  checkedShaders: number;
  checkedRecords: number;
  checkedMarkers: number;
  metadataEdits: number;
  issues: Array<{ kind: string; shader_index?: number; record_index?: number; material_index?: number; name?: string; problem?: string }>;
}
export interface Integrity {
  name_records_checked: number;
  name_hash_mismatches: number[];
  shader_trailers_checked: number;
  shader_trailer_mismatches: number[];
}
export interface Group {
  index: number;
  record_count: number;
  unique_shader_count: number;
  stages: Record<string, number>;
  shader_models: Record<string, number>;
}
export interface DocumentInfo {
  revision?: number;
  exported_revision?: number;
  source: string;
  size: number;
  sha256: string;
  game: string;
  resource_version: number;
  record_size: number;
  header_layout: "uniform" | "per-target";
  runtime_cache_keys: boolean;
  material_header_bytes: number;
  record_count: number;
  shader_count: number;
  stages: Record<string, number>;
  shader_models: Record<string, number>;
  candidate_groups: Group[];
  dirty: boolean;
  integrity: Integrity;
  changes: { shaders: number[]; names: number; references: number; metadata: number };
  shaders: Shader[];
  passes: Pass[];
  metadata: Metadata[];
}
export interface DocumentTab {
  id: string;
  label: string;
  source: string | null;
  dirty: boolean;
  mdfSource: string | null;
  busy: boolean;
  modal: boolean;
}
// 根容器只接触生命周期能力；编辑、编译结果和选择状态始终归标签自己所有。
export interface DocumentController {
  ready: Promise<boolean>;
  loadMmtr: (path: string) => Promise<unknown>;
  loadMdf: (path: string) => Promise<unknown>;
  confirmClose: () => Promise<boolean>;
}
export type DocumentState = Pick<DocumentTab, "source" | "mdfSource" | "dirty" | "busy" | "modal">;
export type Tab = "overview" | "passes" | "shaders" | "metadata" | "source" | "mdf";
export interface MdfProperty {
  name: string;
  offset: number;
  count: number;
  byte_size: number;
  flags: number;
  raw_hex: string;
  values: number[];
}
export interface MdfTexture {
  name: string;
  path: string;
}
export interface MdfMaterialSummary {
  index: number;
  name: string;
  master: string;
  shader_type: number;
  flags: number[];
  flags_hex: string[];
  property_count: number;
  texture_count: number;
  mmtr_linked?: boolean;
}
export interface MdfBindingProperty {
  name: string;
  mmtr_offset: number;
  mmtr_size: number;
  mdf_offset: number | null;
  mdf_size: number | null;
  status: string;
}
export interface MdfBindingTexture {
  name: string;
  path: string;
  status: string;
}
export interface MdfBinding {
  comparison_mode?: "master" | "candidate";
  material_index: number;
  material_name: string;
  master: string;
  shared_material_count: number;
  status: string;
  mmtr: { source: string; sha256: string; user_material_size: number | null } | null;
  properties: MdfBindingProperty[];
  missing_mdf_properties: Array<{ name: string; mmtr_offset: number; mmtr_size: number }>;
  extra_mdf_properties: Array<{ name: string; offset: number; byte_size: number }>;
  textures: MdfBindingTexture[];
  missing_mdf_textures: Array<{ name: string }>;
  extra_mdf_textures: MdfBindingTexture[];
  flags: { shader_type: number; a: string; b: string };
}
export interface MdfMigration {
  material_name: string;
  shared_material_count?: number;
  source_mmtr: { source: string; sha256: string };
  target_mmtr: { source: string; sha256: string };
  safe_to_auto_migrate: boolean;
  semantic_equivalence_verified: boolean;
  target_material_texture_requirements: string;
  blockers: string[];
  limitations: string[];
  structural_mdf_repack_required: boolean;
  summary: Record<string, number>;
  status_catalog: Record<string, string>;
  property_mapping: Array<{ name: string; status: string; source_mmtr_offset: number | null; target_mmtr_offset: number; byte_size: number; source_byte_size: number | null; mdf_offset: number | null; mdf_byte_size: number | null; source_values: number[] | null }>;
  source_only_properties: Array<{ name: string; source_offset: number; byte_size: number; status: string }>;
  extra_mdf_properties: Array<{ name: string; mdf_offset: number; byte_size: number; status: string }>;
  texture_mapping: Array<{ name: string; status: string; source_paths: string[] }>;
}
export interface MdfMaterial extends MdfMaterialSummary {
  offset: number;
  property_block_size: number;
  properties: MdfProperty[];
  textures: MdfTexture[];
  name_hash: string;
  shader_lod: number;
  bake_texture_array_size: number;
  gpbf_name_count: number;
  gpbf_path_count: number;
  gpbf: MdfTexture[];
  mmtrs: number[][];
}
export type MdfSetting = "shader_type" | "shader_lod" | "bake_texture_array_size" | "flags_a" | "flags_b";
export interface MdfDocument {
  source: string;
  size: number;
  sha256: string;
  version: number;
  game: string;
  material_count: number;
  materials: MdfMaterialSummary[];
  selected: MdfMaterial | null;
  binding: MdfBinding | null;
  dirty: boolean;
  unsaved: boolean;
  changes: Array<Record<string, unknown>>;
  migration: MdfMigration | null;
}
export interface DialogState {
  title: string;
  description: string;
  value: string;
  kind: "text" | "confirm" | "open" | "save" | "parameter" | "mdf-property" | "mdf-texture" | "resource";
  confirmLabel: string;
  placeholder?: string;
  multiple?: boolean;
  parameter?: { name: string; byteSize: string; byteOffset: string };
  fields?: Record<string, string>;
  selectOptions?: Array<{ value: string; label: string; size?: number }>;
}
export interface SourceInput {
  source: string;
  entry: string;
  profile: string;
}
export interface CompileResult {
  size: number;
  warnings: string;
  ir: string;
}
export interface ReconstructionResult extends SourceInput {
  origin_ir?: string;
  source_map?: number[][];
  origin_shader_index?: number;
  origin_shader_sha256?: string;
  origin_document?: string;
}
export interface ReconstructionTrace {
  baseline: SourceInput;
  ir: string;
  sourceMap: number[][];
  shaderIndex: number;
  shaderSha256: string;
  documentSource: string;
}
export const hex = (n: number) => "0x" + n.toString(16).toUpperCase();
export const bytes = (n: number) =>
  n >= 1_048_576
    ? (n / 1_048_576).toFixed(2) + " MB"
    : n.toLocaleString() + " B";
export const basename = (p: string) => p.split(/[\\/]/).pop() ?? p;
