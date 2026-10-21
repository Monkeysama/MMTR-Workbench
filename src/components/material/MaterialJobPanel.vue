<script setup lang="ts">
import { computed } from "vue";
import { ElButton, ElProgress } from "element-plus";
import { type MaterialJob, terminalJob } from "../../materialBatch";
// 只展示核心阶段进度；不把单个阶段百分比解释为整个任务的剩余时间。
const props = defineProps<{ job: MaterialJob }>();
defineEmits<{ cancel: [] }>();
const labels: Record<string, string> = {
  queued: "等待执行", preparing: "准备候选", starting: "启动 DXC", planning: "核对计划",
  shaders: "处理 Shader", building: "重建 MMTR", validating: "验证接口",
  validated: "候选已验证", committing: "核对源状态", complete: "已提交",
  candidate_ready: "候选已验证", exporting: "正在导出", exported: "变体已导出", discarded: "候选已丢弃",
};
const states: Record<string, string> = {
  succeeded: "完成", failed: "失败", cancelled: "已取消", stale: "源状态已改变", cancelling: "正在取消",
};
const label = computed(() => (props.job.scope === "independent_variant" && props.job.state === "succeeded")
  ? labels[props.job.stage] ?? props.job.stage : states[props.job.state] ?? labels[props.job.stage] ?? props.job.stage);
const percent = computed(() => props.job.total ? Math.round(props.job.completed / props.job.total * 100) : 0);
</script>
<template>
  <section class="job-panel" aria-live="polite">
    <div class="job-heading">
      <strong>{{ label }}</strong>
      <span v-if="job.total">{{ job.completed }} / {{ job.total }}</span>
      <span class="job-time">{{ job.elapsed_seconds.toFixed(1) }} 秒</span>
      <ElButton v-if="!terminalJob(job)" :disabled="job.state === 'cancelling'" @click="$emit('cancel')">取消任务</ElButton>
    </div>
    <ElProgress v-if="!terminalJob(job)" :percentage="percent" :show-text="false" :indeterminate="!job.total" />
  </section>
</template>
<style scoped>
.job-panel { padding: 14px; border: 1px solid var(--el-border-color); border-radius: 6px; }
.job-heading { display: flex; gap: 16px; align-items: center; margin-bottom: 8px; }
.job-time { margin-left: auto; color: var(--el-text-color-secondary); }
</style>
