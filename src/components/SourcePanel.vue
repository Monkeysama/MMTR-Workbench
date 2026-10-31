<script setup lang="ts">
import { computed, shallowRef, watch } from "vue";
import {
  ElInput,
  ElButton,
  ElTag,
  ElTabs,
  ElTabPane,
  ElAlert,
} from "element-plus";
import { Play, Download, Replace } from "lucide-vue-next";
import type { CompileResult, Shader, SourceInput, ReconstructionTrace } from "../types";
import CodeViewer from "./CodeViewer.vue";
import SourceTracePanel from "./SourceTracePanel.vue";
import { traceIsCurrent } from "../lib/sourceTrace";
const props = defineProps<{
  source: SourceInput;
  result: CompileResult | null;
  trace: ReconstructionTrace | null;
  shader: Shader | null;
  busy: boolean;
}>();
const emit = defineEmits<{
  update: [value: Partial<SourceInput>];
  compile: [];
  export: [];
  apply: [];
  sync: [];
  batchSync: [];
  profile: [];
}>();
const view = shallowRef("source");
const selectedLine = shallowRef(0);
const traceActive = computed(() => traceIsCurrent(props.trace, props.source));
watch(() => props.trace, () => { selectedLine.value = 0; });
watch(
  () => props.result,
  (result) => {
    if (!result) view.value = "source";
  },
);
</script>
<template>
  <div class="feature-stack">
    <ElAlert
      class="source-note"
      type="info"
      :closable="false"
      show-icon
      title="支持已验证范围内的 PS、VS、CS 和 MS 重建。编译后会检查阶段、Shader Model、资源和输入输出；未知操作会明确提示。"
    />
    <section class="panel source-panel">
      <div class="table-toolbar source-toolbar">
        <label
          >入口<ElInput
            :model-value="source.entry"
            aria-label="HLSL 入口"
            @update:model-value="emit('update', { entry: $event })" /></label
        ><label
          >Profile<ElInput
            :model-value="source.profile"
            aria-label="HLSL Profile"
            @update:model-value="emit('update', { profile: $event })" /></label
        ><ElButton :disabled="!shader || busy" @click="emit('profile')"
          >使用目标阶段</ElButton
        ><ElTag
          class="push-right"
          :type="shader ? 'primary' : 'info'"
          effect="plain"
          >{{ shader ? `目标 Shader #${shader.index}` : "未选择目标块" }}</ElTag
        >
      </div>
      <ElTabs v-model="view" class="source-tabs"
        ><ElTabPane label="HLSL 源码" name="source"
          ><div class="source-edit-pane"><SourceTracePanel :trace="trace" :source="source" :line="selectedLine" />
          <CodeViewer
            :value="source.source"
            editable
            :selectable="traceActive"
            label="HLSL 源代码"
            @select-line="selectedLine = $event"
            @update="emit('update', { source: $event })" /></div></ElTabPane
        ><ElTabPane label="编译结果 · DXIL" name="compiled" :disabled="!result"
          ><CodeViewer
            v-if="result"
            :value="result.ir"
            label="编译结果 DXIL" /></ElTabPane
      ></ElTabs>
      <div class="source-actions">
        <ElButton type="primary" :loading="busy" @click="emit('compile')"
          ><Play :size="17" />编译 HLSL</ElButton
        ><ElButton :disabled="busy || !result" @click="emit('export')"
          ><Download :size="17" />导出 DXBC</ElButton
        ><ElButton :disabled="busy || !result || !shader" @click="emit('apply')"
          ><Replace :size="17" />替换目标块</ElButton
        ><ElButton type="warning" :disabled="busy || !result || !shader" @click="emit('sync')"
          ><Replace :size="17" />同步接口并应用</ElButton
        ><ElButton type="warning" plain :disabled="busy || !result || !shader" @click="emit('batchSync')"
          ><Replace :size="17" />批量同步变体</ElButton
        ><span v-if="result" class="primary-text push-right"
          >编译完成 · {{ result.size.toLocaleString() }} B</span
        >
      </div>
      <pre v-if="result?.warnings" class="compile-log">{{
        result.warnings
      }}</pre>
    </section>
  </div>
</template>

<style scoped>
.source-edit-pane { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.source-edit-pane > .code-workspace { flex: 1; height: auto; min-height: 0; }
@media (max-width: 520px) {
  .source-note { display: none; }
}
</style>
