<script setup lang="ts">
import { computed, shallowRef, watch } from "vue";
import {
  ElInput,
  ElButton,
  ElSwitch,
  ElTag,
  ElTooltip,
  ElDrawer,
  ElTable,
  ElTableColumn,
  ElTabs,
  ElTabPane,
  ElMessage,
  ElAlert,
} from "element-plus";
import {
  Search,
  ChevronUp,
  ChevronDown,
  Copy,
  Database,
  ExternalLink,
} from "lucide-vue-next";
import { convertDxil } from "../lib/dxilReadable";
import CodeViewer from "./CodeViewer.vue";
const props = defineProps<{ ir: string }>();
const emit = defineEmits<{ inspect: [irLine: number] }>();
const compact = shallowRef(true),
  query = shallowRef(""),
  cursor = shallowRef(0),
  selectedLine = shallowRef(0),
  resourcesOpen = shallowRef(false),
  resourceTab = shallowRef("bindings");
const readable = computed(() =>
  convertDxil(props.ir, { foldExpressions: compact.value }),
);
const matches = computed(() => {
  const q = query.value.trim().toLowerCase();
  return q
    ? readable.value.code
        .split("\n")
        .flatMap((line, i) => (line.toLowerCase().includes(q) ? [i + 1] : []))
    : [];
});
const focusLine = computed(() =>
  query.value.trim() ? (matches.value[cursor.value] ?? 0) : selectedLine.value,
);
const originalLines = computed(
  () => readable.value.sourceMap[focusLine.value - 1] ?? [],
);
watch(query, () => {
  cursor.value = 0;
});
watch([compact, () => props.ir], () => {
  cursor.value = 0;
  selectedLine.value = 0;
});
function move(direction: number) {
  cursor.value =
    (cursor.value + direction + matches.value.length) % matches.value.length;
}
function select(line: number) {
  query.value = "";
  selectedLine.value = line;
}
async function copy() {
  try {
    await navigator.clipboard.writeText(readable.value.code);
    ElMessage({ type: "success", message: "可读伪代码已复制" });
  } catch {
    ElMessage.error("无法访问剪贴板，请在代码区选择并复制。");
  }
}
</script>
<template>
  <div class="readable-panel">
    <div class="readable-toolbar">
      <ElInput
        v-model="query"
        class="readable-search"
        aria-label="搜索伪代码"
        placeholder="搜索参数、贴图或表达式…"
        clearable
        ><template #prefix><Search :size="16" /></template
      ></ElInput>
      <div v-if="query" class="code-match-controls">
        <span>{{ matches.length ? cursor + 1 : 0 }}/{{ matches.length }}</span
        ><ElButton
          text
          :disabled="!matches.length"
          aria-label="上一处伪代码匹配"
          @click="move(-1)"
          ><ChevronUp :size="16" /></ElButton
        ><ElButton
          text
          :disabled="!matches.length"
          aria-label="下一处伪代码匹配"
          @click="move(1)"
          ><ChevronDown :size="16"
        /></ElButton>
      </div>
      <ElTooltip content="仅合并同一基本块内的单次使用、无副作用表达式"
        ><label class="compact-label"
          >合并表达式<ElSwitch
            v-model="compact"
            aria-label="合并表达式" /></label
      ></ElTooltip>
      <ElButton
        text
        aria-label="查看着色器资源映射"
        @click="resourcesOpen = true"
        ><Database :size="17"
      /></ElButton>
      <ElButton text aria-label="复制可读伪代码" @click="copy"
        ><Copy :size="17"
      /></ElButton>
    </div>
    <div class="readable-summary">
      <ElTag effect="plain">HLSL 风格伪代码</ElTag
      ><span>{{ readable.stats.translated.toLocaleString() }} 条已翻译</span
      ><span v-if="readable.stats.namedDxCalls"
        >{{ readable.stats.namedDxCalls }} 条保留 DX 调用</span
      ><ElTag v-if="readable.stats.raw" type="warning" effect="plain"
        >{{ readable.stats.raw }} 条未识别</ElTag
      ><span class="readable-hint">只读 · 不直接回编译</span>
    </div>
    <CodeViewer
      :value="readable.code"
      :focus-line="focusLine"
      selectable
      label="可读伪代码"
      @select-line="select"
    />
    <div class="readable-trace">
      <span v-if="originalLines.length"
        >代码第 {{ focusLine }} 行 → IR
        {{
          originalLines.length > 5
            ? originalLines.slice(0, 5).join(", ") + "…"
            : originalLines.join(", ")
        }}</span
      ><span v-else>点击代码行，可查看对应的原始 IR</span
      ><ElButton
        text
        type="primary"
        :disabled="!originalLines.length"
        @click="emit('inspect', originalLines[0])"
        ><ExternalLink :size="15" />查看原 IR</ElButton
      >
    </div>
    <ElDrawer
      v-model="resourcesOpen"
      title="着色器反射映射"
      size="min(760px, 90vw)"
      append-to-body
    >
      <ElTabs v-model="resourceTab"
        ><ElTabPane
          :label="'资源绑定 (' + readable.resources.length + ')'"
          name="bindings"
          ><ElTable :data="readable.resources" stripe
            ><ElTableColumn
              prop="name"
              label="名称"
              min-width="230"
              show-overflow-tooltip /><ElTableColumn
              prop="kind"
              label="类型"
              width="100" /><ElTableColumn
              prop="binding"
              label="寄存器"
              min-width="120" /><ElTableColumn
              prop="dimension"
              label="维度"
              width="100" /></ElTable></ElTabPane
        ><ElTabPane
          :label="'常量字段 (' + readable.fields.length + ')'"
          name="fields"
          ><ElAlert
            type="info"
            title="名称来自当前编译块的 DXC 反射，不代表原始局部变量名。"
            :closable="false" /><ElTable :data="readable.fields" stripe
            ><ElTableColumn
              prop="buffer"
              label="缓冲"
              min-width="160" /><ElTableColumn
              prop="name"
              label="字段"
              min-width="200"
              show-overflow-tooltip /><ElTableColumn
              prop="type"
              label="类型"
              width="100" /><ElTableColumn
              prop="offset"
              label="字节偏移"
              width="100" /></ElTable></ElTabPane
      ></ElTabs>
    </ElDrawer>
  </div>
</template>
<style scoped>
.readable-panel {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.readable-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  padding: 10px 14px;
  border-bottom: 1px solid var(--edge);
  flex-shrink: 0;
}
.readable-search {
  flex: 1;
  min-width: 170px;
}
.compact-label {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
  color: var(--muted);
}
.code-match-controls {
  display: flex;
  align-items: center;
  color: var(--muted);
  font-size: 13px;
}
.code-match-controls .el-button {
  padding: 4px;
  width: 26px;
}
.readable-toolbar > .el-button {
  padding: 5px;
}
.readable-summary {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  padding: 8px 14px;
  font-size: 13px;
  color: var(--muted);
  flex-shrink: 0;
}
.readable-hint {
  margin-left: auto;
}
.readable-panel > .code-workspace {
  flex: 1;
  min-height: 0;
}
.readable-trace {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 5px 12px;
  font-size: 13px;
  color: var(--muted);
  border-top: 1px solid var(--edge);
  flex-shrink: 0;
}
.readable-trace > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
