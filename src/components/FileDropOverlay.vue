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
.file-drop-overlay { position: fixed; inset: 12px; z-index: 3000; display: grid; place-items: center; pointer-events: none; border: 3px dashed var(--el-color-primary); border-radius: 10px; background: rgb(240 245 255 / 92%); color: var(--el-color-primary); }
.file-drop-card { display: flex; flex-direction: column; align-items: center; gap: 16px; max-width: 80%; text-align: center; }
.file-drop-card strong { font-size: 26px; }
.invalid { color: var(--el-color-warning); border-color: var(--el-color-warning); background: rgb(255 250 240 / 94%); }
</style>
