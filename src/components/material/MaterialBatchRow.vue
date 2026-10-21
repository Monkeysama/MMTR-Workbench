<script setup lang="ts">
import { ElButton, ElInput, ElOption, ElSelect, ElTooltip } from "element-plus";
import { Trash2 } from "lucide-vue-next";
import type { MaterialDraft } from "../../materialBatch";
// 一行动作表单；通过事件替换父级草稿，不直接修改 props。
const props = defineProps<{ row: MaterialDraft; disabled: boolean }>();
const emit = defineEmits<{ update: [patch: Partial<MaterialDraft>]; remove: [] }>();
function width(value: number) {
  emit("update", { byteSize: value as MaterialDraft["byteSize"], values: Array(value / 4).fill(0).join(", ") });
}
</script>
<template>
  <div class="batch-row">
    <span class="row-kind">{{ row.action === 'parameter' ? '参数' : '贴图' }}</span>
    <ElInput :model-value="row.name" :disabled="disabled" aria-label="接口名称" placeholder="接口名称"
      @update:model-value="emit('update', { name: $event })" />
    <template v-if="row.action === 'parameter'">
      <ElSelect :model-value="row.byteSize" :disabled="disabled" aria-label="参数类型" @update:model-value="width">
        <ElOption v-for="n in 4" :key="n" :label="n === 1 ? 'float' : 'float' + n" :value="n * 4" />
      </ElSelect>
      <ElInput :model-value="row.values" :disabled="disabled" aria-label="参数默认值" placeholder="默认值，用逗号分隔"
        @update:model-value="emit('update', { values: $event })" />
    </template>
    <ElInput v-else class="texture-path" :model-value="row.path" :disabled="disabled" aria-label="贴图路径"
      @update:model-value="emit('update', { path: $event })" />
    <ElTooltip content="移除此项"><ElButton :disabled="disabled" aria-label="移除此项" @click="emit('remove')">
      <Trash2 :size="16" />
    </ElButton></ElTooltip>
  </div>
</template>
<style scoped>
.batch-row { display: grid; grid-template-columns: 38px minmax(150px, 1fr) 110px minmax(160px, 1fr) 34px; gap: 8px; align-items: center; }
.row-kind { color: var(--el-text-color-secondary); font-size: 12px; }
.texture-path { grid-column: span 2; }
</style>
