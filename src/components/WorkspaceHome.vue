<script setup lang="ts">
import { Code2, ShieldCheck, Upload } from "lucide-vue-next";

// 空工作区提供文件提示和常用工具入口，操作状态仍由工作区持有。
defineEmits<{ source: []; support: [] }>();
</script>

<template>
  <section class="workspace-home" aria-label="空工作区">
    <div class="home-hint">
      <Upload :size="28" :stroke-width="1.25" aria-hidden="true" />
      <p>拖入 MMTR 或 MDF 文件</p>
      <small>也可使用顶部的打开按钮</small>
      <div class="home-shortcuts">
        <button type="button" @click="$emit('support')"><ShieldCheck :size="16" aria-hidden="true" />支持游戏与格式</button>
        <button type="button" @click="$emit('source')"><Code2 :size="16" aria-hidden="true" />HLSL 编辑器</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.workspace-home {
  display: grid;
  flex: 1;
  place-items: center;
  min-height: 240px;
  padding: 32px 16px;
  text-align: center;
}
.home-hint { color: var(--muted); }
.home-hint > svg { color: var(--dim); }
.home-hint p { margin: 18px 0 8px; font-size: 14px; line-height: 1.7; }
.home-hint small { color: var(--dim); font-size: 13px; line-height: 1.7; }
.home-shortcuts { display: flex; justify-content: center; flex-wrap: wrap; gap: 10px; margin-top: 24px; }
.home-shortcuts button {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  padding: 6px 12px;
  border: 1px solid var(--edge-strong);
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.home-shortcuts button:hover { border-color: var(--accent-line); background: var(--surface-raised); color: var(--ink-strong); }
.home-shortcuts button:focus-visible { outline: 1px solid var(--accent); outline-offset: 3px; }
</style>
