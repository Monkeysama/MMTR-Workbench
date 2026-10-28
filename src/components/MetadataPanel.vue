<script setup lang="ts">
import { computed, shallowRef, watch } from "vue";
import {
  ElInput,
  ElSelect,
  ElOption,
  ElButton,
  ElTag,
  ElTable,
  ElTableColumn,
  ElPagination,
  ElAlert,
  ElTooltip,
} from "element-plus";
import { Search, Download, Pencil, Trash2, Layers3, Plus, ShieldCheck } from "lucide-vue-next";
import { hex, type DocumentInfo, type Metadata, type InterfaceValidation } from "../types";
const props = defineProps<{ doc: DocumentInfo; busy: boolean; batchEnabled: boolean; validating: boolean; validation: InterfaceValidation | null }>();
type ResourceKind = "srvs" | "samplers" | "uavs";
const emit = defineEmits<{ rename: [row: Metadata]; batch: [action: "parameter" | "texture"]; remove: [row: Metadata]; "safe-remove": [action: { action: "parameter" | "texture"; name: string }]; "add-resource": [kind: ResourceKind]; "remove-resource": [kind: ResourceKind]; validate: [scope: InterfaceValidation["scope"]]; export: [] }>();
const validationLabel = computed(() => props.validation?.scope === "associated_layout" ? "关联布局" : "metadata 修改");
const search = shallowRef(""),
  buffer = shallowRef("UserMaterial"),
  page = shallowRef(1);
const buffers = computed(() =>
  [...new Set(props.doc.metadata.map((m) => m.buffer || m.kind))].sort(),
);
const knownMetadata = shallowRef(new Set(props.doc.metadata.map(row => `${row.kind}:${row.buffer}:${row.name}:${row.byteOffset ?? ""}`)));
const resourceKind = computed<ResourceKind | null>(() =>
  buffer.value === "srvs" || buffer.value === "samplers" || buffer.value === "uavs"
    ? buffer.value
    : null,
);
const resourceLabel = computed(() =>
  resourceKind.value === "srvs" ? "SRV" : resourceKind.value === "samplers" ? "Sampler" : "UAV",
);
const filtered = computed(() =>
  props.doc.metadata.filter(
    (m) =>
      (buffer.value === "all" ||
        m.buffer === buffer.value ||
        m.name === buffer.value ||
        (!m.buffer && m.kind === buffer.value)) &&
      `${m.name} ${m.buffer} ${m.kind}`
        .toLowerCase()
        .includes(search.value.toLowerCase()),
  ),
);
const rows = computed(() =>
  filtered.value.slice((page.value - 1) * 60, page.value * 60),
);
watch([search, buffer], () => (page.value = 1));
// metadata 新增后自动切换到所属缓冲，并用搜索框定位新成员或资源。
watch(() => props.doc.source, () => {
  knownMetadata.value = new Set(props.doc.metadata.map(row => `${row.kind}:${row.buffer}:${row.name}:${row.byteOffset ?? ""}`));
});
watch(() => props.doc.metadata.map(row => `${row.kind}:${row.buffer}:${row.name}:${row.byteOffset ?? ""}`), (keys) => {
  const added = props.doc.metadata.find(row => !knownMetadata.value.has(`${row.kind}:${row.buffer}:${row.name}:${row.byteOffset ?? ""}`));
  if (added) {
    buffer.value = added.buffer || added.kind;
    search.value = added.name;
  }
  knownMetadata.value = new Set(keys);
}, { deep: true });
watch(
  () => filtered.value.length,
  (n) => (page.value = Math.min(page.value, Math.max(1, Math.ceil(n / 60)))),
);
</script>
<template>
  <div class="feature-stack">
    <ElAlert
      type="info"
      :closable="false"
      show-icon
      title="这里展示名称与布局；批量新增和安全删除会同步 Shader 与已知关联 MDF。"
    />
    <section class="panel data-panel">
      <div class="table-toolbar metadata-toolbar">
        <ElInput
          v-model="search"
          class="filter-input"
          placeholder="搜索参数或资源名称…"
          aria-label="搜索参数"
          clearable
          ><template #prefix><Search :size="17" /></template></ElInput
        ><ElSelect
          v-model="buffer"
          class="buffer-select"
          aria-label="筛选缓冲或资源"
          ><ElOption label="全部缓冲与资源" value="all" /><ElOption
            v-for="b in buffers"
            :key="b"
            :label="b"
            :value="b" /></ElSelect
        ><ElTooltip :content="batchEnabled ? '新增参数与贴图（支持批量）' : '先打开 MDF 并确认关联材质'"><span><ElButton :disabled="busy || !batchEnabled" aria-label="新增参数与贴图" @click="emit('batch', resourceKind === 'srvs' ? 'texture' : 'parameter')"
          ><Layers3 :size="17" /></ElButton></span></ElTooltip
        ><ElTooltip v-if="resourceKind && resourceKind !== 'srvs'" :content="`新增 ${resourceLabel}`"><span><ElButton :disabled="busy" :aria-label="`新增 ${resourceLabel}`" @click="emit('add-resource', resourceKind!)"
          ><Plus :size="17" /></ElButton></span></ElTooltip
        ><ElTooltip v-if="resourceKind && resourceKind !== 'srvs'" :content="`删除 ${resourceLabel}`"><span><ElButton :disabled="busy" :aria-label="`删除 ${resourceLabel}`" @click="emit('remove-resource', resourceKind!)"
          ><Trash2 :size="17" /></ElButton></span></ElTooltip
        ><ElTooltip content="只验证 metadata 修改；没有修改时不检查 Shader"><span><ElButton :disabled="busy" :loading="validating" aria-label="验证 metadata 修改" @click="emit('validate', 'metadata_delta')"
          ><ShieldCheck :size="17" /></ElButton></span></ElTooltip
        ><ElTooltip :content="batchEnabled ? '逐 Shader 检查当前 MMTR、关联 MDF 和布局标记；未知访问会失败' : '先打开并关联 MDF' "><span><ElButton :disabled="busy || !batchEnabled" :loading="validating" @click="emit('validate', 'associated_layout')">验证关联布局</ElButton></span></ElTooltip
        ><ElTooltip content="导出 CSV"><span><ElButton class="push-right" :disabled="busy" aria-label="导出 CSV" @click="emit('export')"
          ><Download :size="17" /></ElButton></span></ElTooltip
        >
      </div>
      <ElAlert v-if="validation" :type="validation.state === 'passed' ? 'success' : validation.state === 'failed' ? 'error' : 'info'" :closable="false"
        :title="validation.state === 'skipped' ? '未执行验证 · 没有 metadata 修改' : `${validationLabel}验证${validation.state === 'passed' ? '通过' : '失败'} · 检查 ${validation.checkedShaders} 个 Shader、${validation.checkedRecords} 条记录`">
        <template v-if="validation.state === 'failed'"><ul class="validation-issues"><li v-for="(issue, index) in validation.issues" :key="`${issue.kind}:${issue.name}:${index}`">{{ issue.problem ?? `${issue.kind} ${issue.name ?? ''}` }}<span v-if="issue.shader_index !== undefined"> · Shader {{ issue.shader_index }}</span></li></ul></template>
        <template v-else-if="validation.state === 'skipped'">检查 0 个 Shader；未验证关联 MDF 或布局标记。可打开关联 MDF 后选择“验证关联布局”。</template>
        <template v-else-if="validation.scope === 'associated_layout'">已核对当前已知材质及有界 Shader 读取，验证 {{ validation.checkedMarkers }} 处布局标记；未检查其它 MDF 或游戏画面。</template>
        <template v-else>只验证本次 metadata 修改及反射接口；未验证关联 MDF 或完整材质布局。</template>
      </ElAlert>
      <ElTable :data="rows" stripe height="100%" class="workbench-table"
        ><ElTableColumn
          prop="name"
          label="名称"
          min-width="280"
          show-overflow-tooltip
          ><template #default="{ row }"
            ><span class="mono">{{ row.name }}</span></template
          ></ElTableColumn
        ><ElTableColumn
          label="类型 / 缓冲"
          min-width="170"
          show-overflow-tooltip
          ><template #default="{ row }"
            ><ElTag type="info" effect="plain">{{
              row.buffer || row.kind
            }}</ElTag></template
          ></ElTableColumn
        ><ElTableColumn label="缓冲偏移" width="120"
          ><template #default="{ row }"
            ><span class="mono">{{
              row.byteOffset === null ? "—" : hex(row.byteOffset)
            }}</span></template
          ></ElTableColumn
        ><ElTableColumn label="大小" width="95"
          ><template #default="{ row }">{{
            row.size === null ? "—" : `${row.size} B`
          }}</template></ElTableColumn
        ><ElTableColumn label="记录位置" width="130"
          ><template #default="{ row }"
            ><span class="mono secondary-text">{{
              hex(row.offset)
            }}</span></template
          ></ElTableColumn
        ><ElTableColumn label="编辑" width="100" fixed="right"
          ><template #default="{ row }"
            ><div class="metadata-actions"><ElTooltip content="修改共享名称"
              ><ElButton
                text
                type="primary"
                :disabled="busy"
                :aria-label="`修改名称 ${row.name}`"
                @click="emit('rename', row as Metadata)"
                ><Pencil :size="17" /></ElButton></ElTooltip><ElTooltip
              v-if="row.kind === 'member' && row.buffer === 'UserMaterial' || row.kind === 'srvs'"
              :content="!batchEnabled ? '先打开 MDF 并确认关联材质' : row.kind === 'srvs' ? '安全删除贴图槽' : '安全删除参数'"
              ><span><ElButton
                text
                type="danger"
                :disabled="busy || !batchEnabled"
                :aria-label="`安全删除 ${row.name}`"
                @click="emit('safe-remove', { action: row.kind === 'srvs' ? 'texture' : 'parameter', name: row.name })"
                ><Trash2 :size="17" /></ElButton></span></ElTooltip></div></template></ElTableColumn
      ></ElTable>
      <footer class="table-footer">
        <span class="secondary-text"
          >{{ filtered.length }} 项 · 名称编辑保持容量，共享引用同步修改</span
        ><ElPagination
          v-model:current-page="page"
          :page-size="60"
          :total="filtered.length"
          layout="prev, pager, next"
          :pager-count="5"
          background
        />
      </footer>
    </section>
  </div>
</template>

<style scoped>
.metadata-toolbar { gap: 8px; white-space: nowrap; }
.metadata-toolbar :deep(.filter-input) { width: auto; min-width: 120px; flex: 1 1 240px; }
.metadata-toolbar :deep(.buffer-select) { width: 190px; flex: 0 1 210px; }
.metadata-toolbar :deep(.el-button) { margin: 0; }
.metadata-actions { display: flex; align-items: center; justify-content: center; gap: 2px; white-space: nowrap; }
.metadata-actions :deep(.el-button) { margin: 0; padding: 4px; }
.validation-issues { max-height: 160px; overflow: auto; margin: 6px 0 0; padding-left: 18px; }
.data-panel > .el-alert { flex-shrink: 0; }
</style>
