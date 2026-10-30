<script setup lang="ts">
import { FileJson, LoaderCircle, Plus, X } from "lucide-vue-next";
import { nextTick } from "vue";
import type { DocumentTab } from "../types";

const props = defineProps<{
  tabs: DocumentTab[];
  activeId: string;
  active: boolean;
  disabled: boolean;
}>();
// 切换后旧视图会隐藏；等待渲染，再将焦点交给新活动视图的标签按钮。
async function move(event: KeyboardEvent, index: number) {
  const last = props.tabs.length - 1;
  const next = event.key === "Home" ? 0 : event.key === "End" ? last
    : event.key === "ArrowLeft" ? (index + last) % props.tabs.length
    : event.key === "ArrowRight" ? (index + 1) % props.tabs.length : null;
  if (next === null || props.disabled) return;
  event.preventDefault();
  emit("select", props.tabs[next].id);
  await nextTick();
  const target = document.querySelector<HTMLElement>('.workspace-tabs[data-active="true"] [role="tab"][aria-selected="true"]');
  target?.focus();
  target?.scrollIntoView({ block: "nearest", inline: "nearest" });
}
const emit = defineEmits<{
  select: [id: string];
  close: [id: string];
  create: [];
}>();
</script>

<template>
  <div class="workspace-tabs" :data-active="props.active" role="tablist" aria-label="打开的文档">
    <div v-for="(item, index) in props.tabs" :key="item.id" class="workspace-tab" :class="{ active: item.id === props.activeId }">
      <button class="workspace-tab-select" type="button" role="tab" :disabled="props.disabled"
        :aria-selected="item.id === props.activeId" :tabindex="item.id === props.activeId ? 0 : -1"
        :title="item.source ?? item.mdfSource ?? item.label" @click="emit('select', item.id)" @keydown="move($event, index)">
        <LoaderCircle v-if="item.busy" :size="16" class="spin" aria-label="任务进行中" /><FileJson v-else :size="16" />
        <span class="workspace-tab-label">{{ item.label }}</span>
        <span v-if="item.dirty" class="workspace-tab-dirty" aria-label="有未保存修改">●</span>
      </button>
      <button class="workspace-tab-close" type="button" :disabled="props.disabled"
        :aria-label="`关闭 ${item.label}`" :title="`关闭 ${item.label}`" @click="emit('close', item.id)"><X :size="14" /></button>
    </div>
    <button class="workspace-new" type="button" :disabled="props.disabled" aria-label="新建文档标签" title="新建文档标签" @click="emit('create')"><Plus :size="16" /></button>
  </div>
</template>

<style scoped>
.workspace-tabs {
  display: flex;
  align-items: stretch;
  gap: 4px;
  min-width: 0;
  overflow-x: auto;
  flex: 1;
}
.workspace-tab { display: flex; align-items: center; flex-shrink: 0; min-width: 0; }
.workspace-new,
.workspace-tab-select,
.workspace-tab-close {
  border: 0;
  background: transparent;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 34px;
  padding: 0 9px;
  white-space: nowrap;
}
.workspace-new:hover,
.workspace-tab:hover,
.workspace-tab.active {
  color: var(--el-color-primary);
  background: var(--el-fill-color-light);
}
.workspace-tab.active { border-bottom: 2px solid var(--el-color-primary); }
.workspace-tab.active .workspace-tab-select { color: var(--el-color-primary); }
.workspace-tab-label { max-width: 190px; overflow: hidden; text-overflow: ellipsis; }
.workspace-tab-dirty { color: var(--el-color-warning); font-size: 12px; }
.workspace-tab-close { padding: 0 5px; }
.workspace-tab-close:hover { background: var(--el-fill-color); color: var(--el-color-danger); }
.workspace-new { flex-shrink: 0; }
button:disabled { opacity: 0.55; cursor: default; }
button:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: -2px; }
@media (max-width: 1100px) { .workspace-tab-label { max-width: 130px; } }
</style>
