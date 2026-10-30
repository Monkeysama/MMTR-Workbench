<script setup lang="ts">
import { shallowRef, watch } from "vue";
import { ElButton, ElInput } from "element-plus";
const props = defineProps<{ value: string; label: string; busy: boolean }>();
const emit = defineEmits<{ save: [value: string] }>();
const draft = shallowRef(props.value);
watch(() => props.value, value => { draft.value = value; });
function save() { if (!props.busy && draft.value !== props.value) emit("save", draft.value); }
</script>
<template>
  <div class="mdf-editable" @keydown.esc="draft = value">
    <ElInput v-model="draft" size="small" :aria-label="label" :title="draft" :disabled="busy" @keydown.enter.prevent="save" />
    <ElButton size="small" :type="draft !== value ? 'primary' : 'default'" :disabled="busy || draft === value" @click="save">应用</ElButton>
  </div>
</template>
<style scoped>
.mdf-editable { display: flex; align-items: center; gap: 5px; }
.mdf-editable :deep(input) { font-family: inherit; font-size: 13px; font-variant-numeric: tabular-nums; }
.mdf-editable :deep(.el-input__inner) { height: 24px; line-height: 24px; }
.mdf-editable .el-button { margin: 0; padding: 0 6px; }
</style>
