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
  <div class="workspace-tabs" :data-active="props.active" role="tablist" aria-label="打开的工作区">
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
    <button class="workspace-new" type="button" :disabled="props.disabled" aria-label="新建工作区" title="新建工作区" @click="emit('create')"><Plus :size="16" /></button>
  </div>
</template>

<style scoped>
.workspace-tabs {
  display: flex;
  align-items: stretch;
  gap: 0;
  min-width: 0;
  flex: 1;
  overflow-x: auto;
  scrollbar-width: none;
}
.workspace-tabs::-webkit-scrollbar { display: none; }
.workspace-tab { position: relative; display: flex; align-items: stretch; flex-shrink: 0; border-right: 1px solid var(--edge); }
.workspace-new,
.workspace-tab-select,
.workspace-tab-close {
  border: 0;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 10px;
  white-space: nowrap;
  font-family: var(--mono);
  font-size: 12px;
  letter-spacing: 0.02em;
}
.workspace-tab-select .lucide { width: 13px; height: 13px; color: var(--dim); }
.workspace-tab-select { min-width: 0; }
.workspace-tab:hover { background: rgba(127, 227, 255, 0.035); }
.workspace-tab.active { background: var(--surface-raised); }
.workspace-tab.active::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 1px;
  background: var(--accent);
}
.workspace-tab.active .workspace-tab-select { color: var(--ink); }
.workspace-tab.active .workspace-tab-select .lucide { color: var(--accent); }
.workspace-tab-label { max-width: 160px; overflow: hidden; text-overflow: ellipsis; }
.workspace-tab-dirty { color: var(--warning); font-size: 9px; }
.workspace-tab-close { width: 25px; padding: 0; color: var(--dim); }
.workspace-tab-close:hover { color: var(--danger); background: transparent; }
.workspace-new { width: 33px; padding: 0; justify-content: center; border-right: 1px solid var(--edge); color: var(--dim); }
.workspace-new:hover { color: var(--accent); background: var(--accent-soft); }
button:disabled { opacity: 0.45; cursor: default; }
button:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
@media (max-width: 1100px) { .workspace-tab-label { max-width: 110px; } }
</style>
