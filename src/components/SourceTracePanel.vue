<script setup lang="ts">
import { computed, shallowRef, watch } from "vue";
import { ElButton, ElDrawer, ElTag } from "element-plus";
import { ExternalLink } from "lucide-vue-next";
import type { ReconstructionTrace, SourceInput } from "../types";
import { basename } from "../types";
import { originalLinesAt, traceIsCurrent } from "../lib/sourceTrace";
import CodeViewer from "./CodeViewer.vue";

const props = defineProps<{ trace: ReconstructionTrace | null; source: SourceInput; line: number }>();
const open = shallowRef(false);
const focusedLine = shallowRef(0);
const active = computed(() => traceIsCurrent(props.trace, props.source));
const originalLines = computed(() => originalLinesAt(props.trace, props.source, props.line));
const status = computed(() => !props.trace ? "重建后可定位对应的原 IR" : !active.value
  ? "源码或编译选项已修改，原映射已停用" : !props.line
  ? "点击源码行或移动光标以定位" : originalLines.value.length
  ? `HLSL 第 ${props.line} 行 · 对应 ${originalLines.value.length} 处 IR`
  : `HLSL 第 ${props.line} 行 · 此行没有直接指令映射`);
watch([active, () => props.trace], () => { open.value = false; focusedLine.value = 0; });
function inspect() {
  if (!originalLines.value.length) return;
  focusedLine.value = originalLines.value[0]!;
  open.value = true;
}
</script>

<template>
  <div class="trace-toolbar">
    <ElTag v-if="trace" effect="plain">重建来源 #{{ trace.shaderIndex }}</ElTag>
    <span class="trace-status" :title="status">{{ status }}</span>
    <ElButton :disabled="!originalLines.length" @click="inspect"><ExternalLink :size="16" />查看原 IR</ElButton>
  </div>
  <ElDrawer v-model="open" :title="`重建时的 DXIL IR · Shader #${trace?.shaderIndex ?? ''}`" size="72%">
    <div v-if="trace" class="trace-inspector">
      <div class="trace-provenance" :title="trace.documentSource">{{ basename(trace.documentSource) }} · {{ trace.shaderSha256.slice(0, 12) }}</div>
      <div class="trace-locations">
        <span>HLSL 第 {{ line }} 行</span>
        <ElButton v-for="original in originalLines" :key="original" :type="focusedLine === original ? 'primary' : 'default'"
          size="small" @click="focusedLine = original">IR 第 {{ original }} 行</ElButton>
      </div>
      <div class="trace-code"><CodeViewer :value="trace.ir" :focus-line="focusedLine" label="重建来源 IR" /></div>
    </div>
  </ElDrawer>
</template>

<style scoped>
.trace-toolbar { display: flex; gap: 12px; align-items: center; padding: 9px 16px; flex-shrink: 0; border-top: 1px solid #e8ebf0; background: #fafbfc; }
.trace-status { flex: 1; min-width: 0; color: #697588; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trace-inspector { height: 100%; display: flex; flex-direction: column; min-height: 0; gap: 12px; }
.trace-provenance { color: #697588; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trace-locations { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; flex-shrink: 0; }
.trace-code { flex: 1; min-height: 0; border-radius: 6px; overflow: hidden; }
</style>
