<script setup lang="ts">
import { FolderOpen } from "lucide-vue-next";
defineProps<{ title: string; description: string; invalid: boolean }>();
</script>

<template>
  <div class="file-drop-overlay" :class="{ invalid }" role="status" aria-live="polite">
    <div class="file-drop-card">
      <FolderOpen :size="44" aria-hidden="true" />
      <strong>{{ title }}</strong>
      <span>{{ description }}</span>
    </div>
  </div>
</template>

<style scoped>
.file-drop-overlay { position: fixed; inset: 12px; z-index: 3000; display: grid; place-items: center; pointer-events: none; border: 1px dashed var(--accent-line); border-radius: 2px; background: rgba(3, 9, 14, 0.9); color: var(--accent); backdrop-filter: blur(6px); }
.file-drop-card { display: flex; flex-direction: column; align-items: center; gap: 14px; max-width: 80%; padding: 28px 34px; text-align: center; background: var(--surface-raised); border: 1px solid var(--accent-line); }
.file-drop-card strong { font-size: 20px; font-weight: 500; color: var(--ink-strong); }
.file-drop-card span { font-size: 13px; color: var(--muted); }
.invalid { color: var(--warning); border-color: rgba(245, 200, 107, 0.45); }
.invalid .file-drop-card { border-color: var(--warning); }
</style>
