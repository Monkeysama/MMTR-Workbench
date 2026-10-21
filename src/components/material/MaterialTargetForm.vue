<script setup lang="ts">
import { ElCheckbox, ElInput, ElButton, ElRadioGroup, ElRadioButton } from "element-plus";
import { FolderOpen } from "lucide-vue-next";
import type { VariantOptions } from "../../materialBatch";
// 共享/独立范围及输出路径；通过事件提交草稿，不修改父级文档或直接执行导出。
const props = defineProps<{ mode: "shared_master" | "independent_variant"; variant: VariantOptions; disabled: boolean }>();
const emit = defineEmits<{
  "update:mode": [value: "shared_master" | "independent_variant"];
  "update:variant": [value: VariantOptions];
  choose: [key: "mmtr_path" | "mdf_path"];
}>();
function update(patch: Partial<VariantOptions>) { emit("update:variant", { ...props.variant, ...patch }); }
</script>
<template>
  <section class="target-form">
    <ElRadioGroup :model-value="mode" :disabled="disabled" aria-label="材质修改范围"
      @update:model-value="emit('update:mode', $event as 'shared_master' | 'independent_variant')">
      <ElRadioButton value="shared_master">修改共享 master</ElRadioButton>
      <ElRadioButton value="independent_variant">创建独立变体</ElRadioButton>
    </ElRadioGroup>
    <template v-if="mode === 'independent_variant'">
      <ElInput :model-value="variant.master" :disabled="disabled" aria-label="变体 master" placeholder="MaterialShader/Variation/name.mmtr"
        @update:model-value="update({ master: $event })" />
      <div v-for="key in (['mmtr_path', 'mdf_path'] as const)" :key="key" class="path-row">
        <ElInput :model-value="variant[key]" :disabled="disabled" :aria-label="key === 'mmtr_path' ? '变体 MMTR 输出' : '变体 MDF 输出'"
          :placeholder="key === 'mmtr_path' ? 'MMTR 输出完整路径，保留资源目录与版本' : 'MDF 输出完整路径'"
          @update:model-value="update({ [key]: $event })" />
        <ElButton :disabled="disabled" :aria-label="key === 'mmtr_path' ? '选择 MMTR 输出' : '选择 MDF 输出'"
          @click="emit('choose', key)"><FolderOpen :size="16" /></ElButton>
      </div>
      <ElCheckbox :model-value="variant.overwrite" :disabled="disabled" @update:model-value="update({ overwrite: !!$event })">
        允许覆盖已有输出文件
      </ElCheckbox>
    </template>
  </section>
</template>
<style scoped>
.target-form { display: flex; flex-direction: column; gap: 10px; }
.path-row { display: flex; gap: 8px; align-items: center; }
</style>
