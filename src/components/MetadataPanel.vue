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
import { Search, Download, Pencil, Trash2 } from "lucide-vue-next";
import { hex, type DocumentInfo, type Metadata } from "../types";
const props = defineProps<{ doc: DocumentInfo; busy: boolean }>();
type ResourceKind = "srvs" | "samplers" | "uavs";
const emit = defineEmits<{ rename: [row: Metadata]; add: []; remove: [row: Metadata]; "add-resource": [kind: ResourceKind]; "remove-resource": [kind: ResourceKind]; validate: []; export: [] }>();
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
      title="这里展示名称与布局；肤色、发光强度等实际值由 MDF 或运行时提供。新增或删除只改 MMTR metadata，必须保证 Shader 字节码已同步。"
    />
    <section class="panel data-panel">
      <div class="table-toolbar">
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
        ><ElButton v-if="buffer === 'UserMaterial'" :disabled="busy" @click="emit('add')"
          >新增参数</ElButton
        ><ElButton v-if="resourceKind" :disabled="busy" @click="emit('add-resource', resourceKind!)"
          >新增 {{ resourceLabel }}</ElButton
        ><ElButton v-if="resourceKind" :disabled="busy" @click="emit('remove-resource', resourceKind!)"
          >删除 {{ resourceLabel }}</ElButton
        ><ElButton :disabled="busy" :loading="busy" title="检查已修改的 MMTR metadata 是否与关联 DXIL 接口一致" @click="emit('validate')"
          >验证 Shader 接口</ElButton
        ><ElButton class="push-right" :disabled="busy" @click="emit('export')"
          ><Download :size="17" />导出 CSV</ElButton
        >
      </div>
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
              v-if="row.kind === 'member' && row.buffer === 'UserMaterial'"
              content="删除参数"
              ><ElButton
                text
                type="danger"
                :disabled="busy"
                :aria-label="`删除参数 ${row.name}`"
                @click="emit('remove', row as Metadata)"
                ><Trash2 :size="17" /></ElButton></ElTooltip></div></template></ElTableColumn
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
.metadata-actions { display: flex; align-items: center; justify-content: center; gap: 2px; white-space: nowrap; }
.metadata-actions :deep(.el-button) { margin: 0; padding: 4px; }
</style>
