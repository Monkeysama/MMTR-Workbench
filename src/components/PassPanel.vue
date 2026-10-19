<script setup lang="ts">
import { computed, shallowRef, watch } from "vue";
import {
  ElInput,
  ElSelect,
  ElOption,
  ElTag,
  ElTable,
  ElTableColumn,
  ElButton,
  ElPagination,
  ElTooltip,
} from "element-plus";
import { Search, ArrowUpRight, GitBranch } from "lucide-vue-next";
import { hex, type DocumentInfo } from "../types";
const props = defineProps<{ doc: DocumentInfo; busy: boolean }>();
const emit = defineEmits<{
  select: [index: number];
  redirect: [record: number, field: number];
}>();
const search = shallowRef(""),
  group = shallowRef("all"),
  page = shallowRef(1);
const filtered = computed(() =>
  props.doc.passes
    .filter(
      (p) =>
        (group.value === "all" || p.group === Number(group.value)) &&
        p.name.toLowerCase().includes(search.value.toLowerCase()),
    )
    .map((p) => ({ ...p, visibleRefs: p.refs.filter((r) => r.field !== 8) })),
);
const rows = computed(() =>
  filtered.value.slice((page.value - 1) * 60, page.value * 60),
);
watch([search, group], () => (page.value = 1));
watch(
  () => filtered.value.length,
  (n) => (page.value = Math.min(page.value, Math.max(1, Math.ceil(n / 60)))),
);
</script>
<template>
  <section class="panel data-panel">
    <div class="table-toolbar">
      <ElInput
        v-model="search"
        class="filter-input"
        placeholder="搜索渲染通道…"
        aria-label="搜索通道"
        clearable
        ><template #prefix><Search :size="17" /></template></ElInput
      ><ElSelect v-model="group" class="filter-select" aria-label="筛选分组"
        ><ElOption label="所有分组" value="all" /><ElOption
          v-for="g in doc.candidate_groups"
          :key="g.index"
          :label="`Group ${g.index}`"
          :value="String(g.index)" /></ElSelect
      ><span class="secondary-text push-right"
        >{{ filtered.length.toLocaleString() }} 条记录</span
      >
    </div>
    <ElTable
      :data="rows"
      stripe
      height="100%"
      class="workbench-table"
      row-key="index"
    >
      <ElTableColumn prop="index" label="索引" width="85"
        ><template #default="{ row }"
          ><span class="mono secondary-text">{{ row.index }}</span></template
        ></ElTableColumn
      >
      <ElTableColumn label="分组" width="100"
        ><template #default="{ row }"
          ><ElTag type="info" effect="plain">G{{ row.group }}</ElTag></template
        ></ElTableColumn
      >
      <ElTableColumn
        prop="name"
        label="通道名称"
        min-width="300"
        show-overflow-tooltip
        ><template #default="{ row }"
          ><span class="mono">{{ row.name }}</span></template
        ></ElTableColumn
      >
      <ElTableColumn label="阶段引用" min-width="350"
        ><template #default="{ row }"
          ><div class="ref-list">
            <div v-for="r in row.visibleRefs" :key="r.field" class="ref-group">
              <ElButton
                :disabled="busy"
                :title="`定位 ${hex(r.field)} 着色器`"
                @click="emit('select', r.shader)"
                ><span>{{ r.stage.toUpperCase() }}</span
                ><b>#{{ r.shader }}</b
                ><ArrowUpRight :size="14" /></ElButton
              ><ElTooltip content="重定向阶段"
                ><ElButton
                  :disabled="busy"
                  :aria-label="`重定向通道 ${row.index} ${r.stage}`"
                  @click="emit('redirect', row.index, r.field)"
                  ><GitBranch :size="15" /></ElButton
              ></ElTooltip>
            </div></div></template
      ></ElTableColumn>
    </ElTable>
    <footer class="table-footer">
      <span class="secondary-text">VS 的两处别名同步更新</span
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
</template>
