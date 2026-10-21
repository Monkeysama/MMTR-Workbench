<script setup lang="ts">
import { computed } from "vue";
import { ElAlert, ElButton, ElDialog, ElOption, ElSelect } from "element-plus";
import type { MaterialDraft, MaterialJob, MaterialPlan, VariantOptions } from "../../materialBatch";
import type { Pass } from "../../types";
import MaterialBatchRow from "./MaterialBatchRow.vue";
import MaterialJobPanel from "./MaterialJobPanel.vue";
import MaterialTargetForm from "./MaterialTargetForm.vue";
// 批次编辑与预览组合；所有请求和计时器由 useMaterialBatch 所有。
const props = defineProps<{
  rows: MaterialDraft[]; record: number | undefined; passes: Pass[];
  plan: MaterialPlan | null; job: MaterialJob | null; error: string;
  working: boolean; loading: boolean; pending: boolean; hasTextures: boolean;
  mode: "shared_master" | "independent_variant"; variant: VariantOptions; notice: string; candidate: boolean;
}>();
const emit = defineEmits<{
  "update:rows": [rows: MaterialDraft[]]; "update:record": [record: number | undefined];
  add: [action: "parameter" | "texture"]; close: []; minimize: []; preview: []; start: []; cancel: []; retry: [];
  "update:mode": [value: "shared_master" | "independent_variant"]; "update:variant": [value: VariantOptions];
  choose: [key: "mmtr_path" | "mdf_path"]; export: []; discard: []; "save-project": []; "read-project": [];
}>();
const records = computed(() => props.passes.filter(pass => pass.refs.some(ref => ref.stage === "ps")));
const knownUsers = computed(() => props.plan?.known_users?.map(row => "#" + row[0] + " " + row[1]).join("、"));
function update(id: number, patch: Partial<MaterialDraft>) {
  emit("update:rows", props.rows.map(row => row.id === id ? { ...row, ...patch } : row));
}
function remove(id: number) { emit("update:rows", props.rows.filter(row => row.id !== id)); }
</script>
<template>
  <ElDialog :model-value="true" title="批量新增材质接口" width="min(920px, 94vw)" class="batch-dialog" top="3vh"
    :close-on-click-modal="false" :close-on-press-escape="!working" :show-close="!working" @close="emit('close')">
    <div class="batch-content">
      <MaterialTargetForm :mode="mode" :variant="variant" :disabled="working"
        @update:mode="emit('update:mode', $event)" @update:variant="emit('update:variant', $event)" @choose="emit('choose', $event)" />
      <ElAlert v-if="mode === 'independent_variant' && candidate" type="success" :closable="false"
        title="候选已验证，尚未导出。导出后才会写入所选的 MMTR 与 MDF 路径。" />
      <div class="batch-actions">
        <ElButton :disabled="working || rows.length >= 100" @click="emit('add', 'parameter')">添加参数</ElButton>
        <ElButton :disabled="working || rows.length >= 100" @click="emit('add', 'texture')">添加贴图</ElButton>
        <ElSelect v-if="hasTextures" :model-value="record" :disabled="working" class="record-select"
          placeholder="选择 PS 绘制记录" aria-label="PS 绘制记录" filterable
          @update:model-value="emit('update:record', $event)">
          <ElOption v-for="pass in records" :key="pass.index" :value="pass.index" :label="'#' + pass.index + ' · ' + pass.name" />
        </ElSelect>
      </div>
      <div class="batch-rows">
        <MaterialBatchRow v-for="row in rows" :key="row.id" :row="row" :disabled="working"
          @update="update(row.id, $event)" @remove="remove(row.id)" />
      </div>
      <section v-if="plan" class="plan-summary">
        <div>参数区 {{ plan.old.parameter_bytes }} → {{ plan.new.parameter_bytes }} B · 贴图 {{ plan.old.texture_names.length }} → {{ plan.new.texture_names.length }}</div>
        <div>同步材质 #{{ plan.target_materials.join('、#') }} · 待分析 {{ plan.shader_candidates.length }} 个 Shader</div>
        <div v-if="knownUsers">已知使用者：{{ knownUsers }}</div>
        <div>当前 MDF 以外的使用者未扫描</div>
        <div v-for="item in plan.parameters" :key="item.name">{{ item.name }} · 偏移 {{ item.byte_offset }} · {{ item.byte_size }} B</div>
      </section>
      <MaterialJobPanel v-if="job" :job="job" @cancel="emit('cancel')" />
      <ElAlert v-if="notice && !(mode === 'independent_variant' && candidate)" type="info" :title="notice" :closable="false" />
      <ElAlert v-if="error" type="error" :title="error" :closable="false" />
    </div>
    <template #footer>
      <ElButton v-if="working" @click="emit('minimize')">收起任务</ElButton>
      <ElButton v-if="!candidate" :disabled="working" @click="emit('close')">关闭</ElButton>
      <ElButton v-if="candidate" :disabled="loading" @click="emit('discard')">丢弃候选</ElButton>
      <ElButton v-if="candidate" type="primary" :loading="loading" @click="emit('export')">导出独立变体</ElButton>
      <template v-else>
      <ElButton :disabled="working" @click="emit('read-project')">打开工程</ElButton>
      <ElButton :disabled="working" @click="emit('save-project')">保存工程</ElButton>
      <ElButton v-if="pending" type="primary" :loading="loading" @click="emit('retry')">重试原请求</ElButton>
      <template v-else>
        <ElButton :disabled="working || !rows.length" :loading="loading && !job" @click="emit('preview')">预览</ElButton>
        <ElButton type="primary" :disabled="working || !plan" @click="emit('start')">执行批次</ElButton>
      </template>
      </template>
    </template>
  </ElDialog>
</template>
<style scoped>
:global(.batch-dialog) { max-height: 94vh; display: flex; flex-direction: column; }
:global(.batch-dialog .el-dialog__body) { min-height: 0; overflow-y: auto; }
:global(.batch-dialog .el-dialog__header), :global(.batch-dialog .el-dialog__footer) { flex-shrink: 0; }
.batch-content { display: flex; flex-direction: column; gap: 16px; }
.batch-actions { display: flex; gap: 8px; align-items: center; }
.record-select { margin-left: auto; width: 360px; }
.batch-rows { display: flex; flex-direction: column; gap: 10px; max-height: 40vh; overflow: auto; }
.plan-summary { padding: 12px; background: var(--el-fill-color-light); line-height: 1.8; border-radius: 6px; }
</style>
