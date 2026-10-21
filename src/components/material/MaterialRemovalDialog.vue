<script setup lang="ts">
import { ElAlert, ElButton, ElDialog, ElTag } from "element-plus";
import type { MaterialJob } from "../../materialBatch";
import type { RemovalAction } from "../../composables/useMaterialRemoval";

const props = defineProps<{
  modelValue: boolean;
  action: RemovalAction | null;
  job: MaterialJob | null;
  eligible: boolean;
  failures: Array<{ shader_index: number; error: string }>;
  error: string;
  loading: boolean;
  working: boolean;
  pending: boolean;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  preview: [];
  remove: [];
  cancel: [];
  retry: [];
}>();
</script>

<template>
  <ElDialog :model-value="modelValue" title="安全删除 MMTR 接口" width="min(640px, 92vw)"
    :close-on-click-modal="false" :show-close="!working" :close-on-press-escape="!working"
    @update:model-value="!working && emit('update:modelValue', $event)">
    <div class="removal-content">
      <p v-if="action">目标：<strong>{{ action.name }}</strong>（{{ action.action === "parameter" ? "UserMaterial 参数" : "SRV 贴图槽" }}）</p>
      <p>先分析关联 Shader 的消费者。只有本档可完整证明没有消费者时，才允许继续；提交会同步 MMTR、关联 MDF 并重编译验证候选。</p>
      <ElAlert v-if="job && !loading && !error" :type="eligible ? 'success' : 'warning'" :closable="false"
        :title="eligible ? '消费者证明通过，可生成并验证删除候选' : '尚未获得可删除证明'" />
      <ElAlert v-if="job && working" type="info" :closable="false" :title="`任务进行中：${job.stage}（${job.completed}/${job.total}）`" />
      <ElAlert v-if="error" type="error" :closable="false" :title="error" />
      <div v-if="job?.state === 'succeeded' && eligible" class="proof-row">
        <ElTag type="success">覆盖内无消费者</ElTag>
        <span>候选验证通过后才会提交到当前会话；真实游戏效果仍需单独验证。</span>
      </div>
      <ul v-if="failures.length" class="removal-failures">
        <li v-for="failure in failures.slice(0, 8)" :key="`${failure.shader_index}:${failure.error}`">
          Shader {{ failure.shader_index }}：{{ failure.error }}
        </li>
      </ul>
    </div>
    <template #footer>
      <ElButton v-if="job && !['succeeded','failed','cancelled','stale'].includes(job.state)" :disabled="loading" @click="emit('cancel')">取消任务</ElButton>
      <ElButton :disabled="working" @click="emit('update:modelValue', false)">关闭</ElButton>
      <ElButton v-if="pending" type="primary" :loading="loading" @click="emit('retry')">重试原请求</ElButton>
      <ElButton v-else-if="!eligible" type="primary" :loading="loading" :disabled="working || !action" @click="emit('preview')">检查使用情况</ElButton>
      <ElButton v-else type="danger" :loading="loading" :disabled="working || job?.operation === 'removal' || job?.state !== 'succeeded'" @click="emit('remove')">删除并验证</ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.removal-content { display: grid; gap: 12px; }
.removal-content p { margin: 0; line-height: 1.6; }
.proof-row { display: flex; align-items: center; gap: 10px; color: var(--el-text-color-secondary); }
.removal-failures { max-height: 180px; overflow: auto; margin: 0; padding-left: 20px; color: var(--el-color-danger); }
</style>
