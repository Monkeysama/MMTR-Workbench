<script setup lang="ts">
import { computed } from "vue";
import {
  ElButton,
  ElTag,
  ElProgress,
  ElDescriptions,
  ElDescriptionsItem,
  ElTable,
  ElTableColumn,
  ElAlert,
} from "element-plus";
import {
  ShieldCheck,
  Boxes,
  Workflow,
  Database,
  ArrowUpRight,
} from "lucide-vue-next";
import { bytes, type DocumentInfo } from "../types";
const props = defineProps<{ doc: DocumentInfo; integrityOk: boolean }>();
const emit = defineEmits<{
  navigate: [tab: "passes" | "shaders" | "metadata"];
}>();
const material = computed(() =>
  props.doc.metadata.filter(
    (m) => m.kind === "member" && m.buffer === "UserMaterial",
  ),
);
const materialSize = computed(
  () =>
    props.doc.metadata.find(
      (m) => m.kind === "cbuffers" && m.name === "UserMaterial",
    )?.size ?? 0,
);
const stages = computed(() =>
  Object.entries(props.doc.stages).sort((a, b) => b[1] - a[1]),
);
const groups = computed(() =>
  props.doc.candidate_groups.map((g) => ({
    ...g,
    models: Object.entries(g.shader_models)
      .map(([k, v]) => `${k} (${v})`)
      .join(" / "),
    stageSummary: Object.entries(g.stages)
      .map(([k, v]) => `${k.toUpperCase()} ${v}`)
      .join(" · "),
  })),
);
</script>
<template>
  <div class="overview-content">
    <section class="metrics-grid" aria-label="文件统计">
      <ElButton class="metric-card" @click="emit('navigate', 'passes')"
        ><span class="metric-content"
          ><span class="metric-label"
            ><Workflow :size="19" />渲染通道<ArrowUpRight :size="16" /></span
          ><strong>{{ doc.record_count.toLocaleString() }}</strong
          ><span class="metric-detail"
            >{{ doc.candidate_groups.length }} 个分组</span
          ></span
        ></ElButton
      >
      <ElButton class="metric-card" @click="emit('navigate', 'shaders')"
        ><span class="metric-content"
          ><span class="metric-label"
            ><Boxes :size="19" />编译着色器<ArrowUpRight :size="16" /></span
          ><strong>{{ doc.shader_count }}</strong
          ><span class="metric-detail">DXBC / DXIL 编译块</span></span
        ></ElButton
      >
      <ElButton class="metric-card" @click="emit('navigate', 'metadata')"
        ><span class="metric-content"
          ><span class="metric-label"
            ><Database :size="19" />材质参数<ArrowUpRight :size="16" /></span
          ><strong>{{ material.length }}</strong
          ><span class="metric-detail"
            >UserMaterial · {{ materialSize }} B</span
          ></span
        ></ElButton
      >
      <div class="metric-card integrity-card">
        <span class="metric-label"><ShieldCheck :size="19" />完整性检查</span
        ><strong :class="integrityOk ? 'primary-text' : 'warning-text'">{{
          integrityOk ? "全部匹配" : "存在差异"
        }}</strong
        ><span class="metric-detail"
          >已检查 {{ doc.integrity.shader_trailers_checked }} 个尾项</span
        >
      </div>
    </section>
    <div class="overview-columns">
      <section class="panel container-panel">
        <div class="panel-heading">
          <h2>容器信息</h2>
          <ElTag type="info" effect="plain">SDF · Little Endian</ElTag>
        </div>
        <ElDescriptions :column="1" border>
          <ElDescriptionsItem label="文件大小">{{
            bytes(doc.size)
          }}</ElDescriptionsItem>
          <ElDescriptionsItem label="通道记录"
            ><span class="mono">0x110</span> 字节 / 条</ElDescriptionsItem
          >
          <ElDescriptionsItem label="名称 Hash"
            ><span class="mono">Murmur3</span> · seed
            0xFFFFFFFF</ElDescriptionsItem
          >
          <ElDescriptionsItem label="着色器尾项"
            ><span class="mono">XXH64</span> · seed
            0x00000000FFFFFFFF</ElDescriptionsItem
          >
          <ElDescriptionsItem label="SHA-256"
            ><span class="hash-value mono">{{
              doc.sha256
            }}</span></ElDescriptionsItem
          >
        </ElDescriptions>
      </section>
      <section class="panel pipeline-panel">
        <div class="panel-heading">
          <h2>阶段分布</h2>
          <span class="secondary-text">{{ doc.shader_count }} 个着色器</span>
        </div>
        <div class="stage-distribution">
          <div v-for="[stage, count] in stages" :key="stage" class="stage-row">
            <ElTag type="info" effect="plain">{{ stage.toUpperCase() }}</ElTag
            ><ElProgress
              :percentage="(count / doc.shader_count) * 100"
              :stroke-width="8"
              :show-text="false"
              color="#4776d0"
            /><span class="stage-count">{{ count }}</span>
          </div>
        </div>
        <div class="panel-footer model-summary">
          <span>Shader Model</span
          ><ElTag
            v-for="(count, model) in doc.shader_models"
            :key="model"
            effect="plain"
            >{{ model }} · {{ count }}</ElTag
          >
        </div>
      </section>
    </div>
    <section class="panel">
      <div class="panel-heading">
        <h2>分组分布</h2>
        <span class="secondary-text">组索引与 Shader Model 独立</span>
      </div>
      <ElTable :data="groups" stripe
        ><ElTableColumn label="分组" width="120"
          ><template #default="{ row }"
            ><ElTag type="info" effect="plain"
              >Group {{ row.index }}</ElTag
            ></template
          ></ElTableColumn
        ><ElTableColumn
          prop="record_count"
          label="通道"
          width="110" /><ElTableColumn
          prop="unique_shader_count"
          label="独立着色器"
          width="135" /><ElTableColumn
          prop="models"
          label="Shader Model"
          min-width="190" /><ElTableColumn
          prop="stageSummary"
          label="阶段"
          min-width="260"
      /></ElTable>
    </section>
    <ElAlert
      class="overview-note"
      type="info"
      :closable="false"
      show-icon
      title="导出保留未知区域，并更新已知引用、长度和 Hash。游戏加载与画面效果需在游戏内验证。"
    />
  </div>
</template>
