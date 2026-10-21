<script setup lang="ts">
import { ElAlert, ElButton } from "element-plus";
import { useWorkspaces } from "../composables/useWorkspaces";
import WorkbenchView from "./WorkbenchView.vue";
import FileDropOverlay from "./FileDropOverlay.vue";

// 工作区只组合标签视图；v-show 保留每个实例的草稿、选择和后台任务轮询。
const workspace = useWorkspaces();
const { tabs, activeId, operating, error } = workspace;
const { active: dropActive, title, description, problem } = workspace.fileDrop;
</script>

<template>
  <div v-for="item in tabs" v-show="item.id === activeId" :key="item.id" class="workspace-document">
  <WorkbenchView
    :document-id="item.id" :tabs="tabs" :active="item.id === activeId" :workspace-busy="operating"
    @ready="workspace.register(item.id, $event)" @state="workspace.update(item.id, $event)"
    @select="workspace.activate" @close="workspace.close" @create="workspace.newTab"
    @open-mmtr="workspace.openFiles" @open-mdf="workspace.attachMdf(item.id, $event)" />
  </div>
  <div v-if="!tabs.length" class="workspace-start">
    <ElButton :loading="operating" @click="workspace.initialize">{{ error ? '重新连接核心' : '正在初始化' }}</ElButton>
  </div>
  <ElAlert v-if="error" class="workspace-error" :title="error" type="error" show-icon @close="error = ''" />
  <FileDropOverlay v-if="dropActive" :title="title" :description="description" :invalid="!!problem" />
</template>

<style scoped>
.workspace-document { height: 100%; }
.workspace-start { display: grid; height: 100%; place-items: center; }
.workspace-error { position: fixed; top: 72px; right: 24px; width: min(680px, calc(100vw - 48px)); z-index: 2100; }
</style>
