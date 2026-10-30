<script setup lang="ts">
import { shallowRef, watch } from "vue";
import { ElInput } from "element-plus";
import { mdfEditValues } from "../../utils/mdfDisplay";
const props = defineProps<{ value: string; label: string; busy: boolean; numeric?: boolean }>();
const emit = defineEmits<{ save: [value: string] }>();
const draft = shallowRef(props.value);
const message = shallowRef("");
let submitted: string | undefined;
let expected: string | undefined;
function canonical(value: string) { return props.numeric ? mdfEditValues([Math.fround(Number(value))]) : value; }
watch(() => props.value, (value, previous) => {
  // An older RPC response must not replace a newer draft or submission.
  if (expected !== undefined && canonical(value) !== expected) return;
  if (draft.value === previous || draft.value === submitted) draft.value = value;
  expected = undefined;
  submitted = undefined;
});
function save() {
  if (props.busy || draft.value === submitted) return;
  if (props.numeric ? !draft.value.trim() || !Number.isFinite(Math.fround(Number(draft.value))) : !draft.value.trim() || draft.value.includes("\0")) {
    message.value = props.numeric ? "请输入有限的 float32 数值" : "纹理路径不能为空或包含 NUL";
    return;
  }
  message.value = "";
  if (canonical(draft.value) === canonical(props.value) && expected === undefined) return;
  submitted = draft.value;
  expected = canonical(draft.value);
  emit("save", draft.value);
}
function cancel() {
  draft.value = submitted ?? props.value;
  message.value = "";
}
</script>
<template>
  <ElInput v-model="draft" class="mdf-auto-input" :class="{ 'is-invalid': message }" size="small" :aria-label="label" :aria-invalid="!!message" :title="message || draft" :disabled="busy" :inputmode="numeric ? 'decimal' : 'text'" @input="message = ''" @change="save" @blur="save" @keydown.enter.prevent="save" @keydown.esc.prevent="cancel" />
</template>
<style scoped>
.mdf-auto-input :deep(input) { font-family: inherit; font-size: 13px; height: 26px; line-height: 26px; font-variant-numeric: tabular-nums; }
.mdf-auto-input.is-invalid :deep(.el-input__wrapper) { outline: 1px solid var(--el-color-danger); outline-offset: -1px; }
</style>
