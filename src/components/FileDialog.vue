<script setup lang="ts">
import { reactive, shallowRef, useTemplateRef, watch } from "vue";
import {
  ElDialog,
  ElInput,
  ElButton,
  ElAlert,
  ElSelect,
  ElOption,
  type InputInstance,
} from "element-plus";
import { open, save } from "@tauri-apps/plugin-dialog";
import { FolderOpen } from "lucide-vue-next";
import { isDesktop } from "../api";
import type { DialogState } from "../types";
const props = defineProps<{ dialog: DialogState }>();
const emit = defineEmits<{ done: [value: string | null] }>();
const value = shallowRef(props.dialog.value);
const parameter = reactive({
  name: props.dialog.parameter?.name ?? "",
  byteSize: props.dialog.parameter?.byteSize ?? "4",
  byteOffset: props.dialog.parameter?.byteOffset ?? "",
});
const fields = reactive({ ...(props.dialog.fields ?? {}) });
const structured = ["parameter", "mdf-property", "mdf-texture", "resource"].includes(props.dialog.kind);
const dialogError = shallowRef("");
const field = useTemplateRef<InputInstance>("field");
watch(() => fields.name, (name) => {
  if (props.dialog.kind !== "mdf-property") return;
  const option = props.dialog.selectOptions?.find((item) => item.value === name);
  if (option?.size && option.size % 4 === 0) fields.values = Array(option.size / 4).fill("0").join(",");
});
function submit() {
  if (structured && props.dialog.kind === "parameter") {
    if (!parameter.name.trim() || !parameter.byteSize.trim() || !parameter.byteOffset.trim()) {
      dialogError.value = "请填写参数名称、字节宽度和缓冲偏移。";
      return;
    }
    emit("done", JSON.stringify(parameter));
    return;
  }
  if (structured) {
    const required = props.dialog.kind === "mdf-property"
      ? ["name", "values"]
      : props.dialog.kind === "mdf-texture"
        ? ["name", "path"]
        : ["record", "name"];
    if (required.some((key) => !(fields[key] ?? "").trim())) {
      dialogError.value = "请填写完整信息。";
      return;
    }
    emit("done", JSON.stringify(fields));
    return;
  }
  if (props.dialog.kind !== "confirm" && !value.value.trim()) {
    dialogError.value =
      props.dialog.kind === "text" ? "请输入编辑值。" : "请输入完整文件路径。";
    return;
  }
  emit("done", value.value);
}
async function browse() {
  try {
    const result =
      props.dialog.kind === "save"
        ? await save({ defaultPath: value.value || undefined })
        : await open({ multiple: false, directory: false });
    if (typeof result === "string") value.value = result;
  } catch (e) {
    dialogError.value = String(e);
  }
}
</script>
<template>
  <ElDialog
    :model-value="true"
    :title="dialog.title"
    width="680px"
    :close-on-click-modal="false"
    align-center
    @close="emit('done', null)"
    @opened="field?.focus()"
  >
    <form @submit.prevent="submit">
      <p class="dialog-description">{{ dialog.description }}</p>
      <div v-if="dialog.kind === 'parameter'" class="parameter-fields">
        <label>参数名称<ElInput v-model="parameter.name" placeholder="例如 NewParam" /></label>
        <label>字节宽度<ElInput v-model="parameter.byteSize" placeholder="4、8、12、16" /></label>
        <label>缓冲偏移<ElInput v-model="parameter.byteOffset" placeholder="按当前缓冲末尾填写" /></label>
      </div>
      <div v-else-if="dialog.kind === 'mdf-property'" class="parameter-fields">
        <label>参数名称<ElSelect v-if="dialog.selectOptions?.length" v-model="fields.name" filterable placeholder="选择 MMTR 参数"><ElOption v-for="option in dialog.selectOptions" :key="option.value" :label="option.label" :value="option.value" /></ElSelect><ElInput v-else v-model="fields.name" placeholder="例如 AlphaAdjust" /></label>
        <label class="wide-field">Float 值（逗号分隔）<ElInput v-model="fields.values" placeholder="例如 1 或 1,1,1,1" /></label>
      </div>
      <div v-else-if="dialog.kind === 'mdf-texture'" class="parameter-fields">
        <label>纹理槽名称<ElSelect v-if="dialog.selectOptions?.length" v-model="fields.name" filterable placeholder="选择 MMTR SRV"><ElOption v-for="option in dialog.selectOptions" :key="option.value" :label="option.label" :value="option.value" /></ElSelect><ElInput v-else v-model="fields.name" placeholder="例如 NormalMap" /></label>
        <label class="wide-field">纹理路径<ElInput v-model="fields.path" placeholder="Textures/example.tex" /></label>
      </div>
      <div v-else-if="dialog.kind === 'resource'" class="parameter-fields">
        <label>记录索引<ElInput v-model="fields.record" placeholder="例如 4" /></label>
        <label class="wide-field">资源名称<ElInput v-model="fields.name" placeholder="例如 NewTexture" /></label>
      </div>
      <div v-else-if="dialog.kind !== 'confirm'" class="path-field">
        <ElInput
          ref="field"
          v-model="value"
          :placeholder="dialog.placeholder"
          :aria-label="dialog.kind === 'text' ? '编辑值' : '文件路径'"
        /><ElButton v-if="isDesktop() && dialog.kind !== 'text'" @click="browse"
          ><FolderOpen :size="17" />浏览</ElButton
        >
      </div>
      <ElAlert
        v-if="dialogError"
        class="page-alert"
        type="error"
        :title="dialogError"
        :closable="false"
        show-icon
      />
      <button type="submit" hidden aria-hidden="true" tabindex="-1" />
    </form>
    <template #footer
      ><ElButton @click="emit('done', null)">取消</ElButton
      ><ElButton type="primary" @click="submit">{{
        dialog.confirmLabel
      }}</ElButton></template
    >
  </ElDialog>
</template>

<style scoped>
.parameter-fields { display: grid; grid-template-columns: 1.4fr .7fr .9fr; gap: 12px; }
.parameter-fields label { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--text-muted); }
.parameter-fields .wide-field { grid-column: span 2; }
</style>
