<script setup lang="ts">
import { computed } from "vue";
import type { DocumentInfo } from "../types";

const props = defineProps<{ doc: DocumentInfo; integrityOk: boolean }>();

const stageLabels: Record<string, string> = {
  vs: "顶点",
  ps: "像素",
  cs: "计算",
  ms: "网格",
  gs: "几何",
  hs: "细分",
  ds: "域",
  as: "放大",
};
const stageRoles: Record<string, string> = {
  vs: "PRIMARY",
  ps: "OUTPUT",
  cs: "COMPUTE",
  ms: "RARE",
  gs: "GEOMETRY",
  hs: "HULL",
  ds: "DOMAIN",
  as: "AMPLIFY",
};
const stageOrder = ["vs", "ps", "cs", "ms", "gs", "hs", "ds", "as"];

// 使用容器自带的阶段计数，不根据记录样本推断缺失阶段。
const stageEntries = computed(() =>
  Object.entries(props.doc.stages)
    .filter(([, count]) => count > 0)
    .sort((a, b) => {
      const rank = (stage: string) => {
        const index = stageOrder.indexOf(stage.toLowerCase());
        return index === -1 ? 99 : index;
      };
      return rank(a[0]) - rank(b[0]) || b[1] - a[1];
    }),
);
const groups = computed(() =>
  props.doc.candidate_groups.map((group) => {
    const stageParts = stageEntries.value
      .map(([stage]) => [stage, group.stages[stage] ?? 0] as [string, number])
      .filter(([, count]) => count > 0);
    return {
      ...group,
      models: Object.entries(group.shader_models)
        .map(([model, count]) => `${model} (${count})`)
        .join(" / "),
      stageParts,
      stageSummary: stageParts.map(([stage, count]) => `${stage.toUpperCase()} ${count}`).join(" · "),
    };
  }),
);
const modelEntries = computed(() =>
  Object.entries(props.doc.shader_models)
    .filter(([, count]) => count > 0)
    .sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true }))
    .map(([model, count], index) => ({
      model,
      count,
      className: `model-seg-${index % 3}`,
      pct: shaderPct(count),
    })),
);
const modelNote = computed(() => {
  const mixed = props.doc.candidate_groups
    .filter((group) => Object.values(group.shader_models).filter((count) => count > 0).length > 1)
    .map((group) => `Group ${group.index}`);
  return mixed.length ? `${mixed.join("、")} 同时包含多个 Shader Model` : "各分组使用单一 Shader Model";
});
const trace = computed(() => {
  const parts: string[] = [];
  const records = props.doc.candidate_groups.map((group) => group.record_count);
  if (records.length && records.every((count) => count === records[0])) {
    parts.push(`Group 0–${records.length - 1} 各 ${records[0]} 条通道`);
  }
  const rare = [...stageEntries.value].sort((a, b) => a[1] - b[1])[0];
  if (rare) {
    const present = props.doc.candidate_groups
      .filter((group) => (group.stages[rare[0]] ?? 0) > 0)
      .map((group) => group.index);
    if (present.length === 1) parts.push(`${rare[0].toUpperCase()} 仅出现在 Group ${present[0]}`);
  }
  const stageCount = stageEntries.value.length;
  if (stageCount && props.doc.candidate_groups.every((group) =>
    Object.values(group.stages).filter((count) => count > 0).length === stageCount)) {
    parts.push(`所有分组均包含 ${stageCount} 个阶段`);
  }
  parts.push(`${props.doc.shader_count} 个唯一着色器，${Object.keys(props.doc.shader_models).length} 个 Shader Model`);
  return parts.join(" · ");
});

function shaderPct(count: number) {
  return props.doc.shader_count ? Math.round((count / props.doc.shader_count) * 100) : 0;
}
function stageLabel(stage: string) {
  return stageLabels[stage.toLowerCase()] ?? stage.toUpperCase();
}
function stageRole(stage: string) {
  return stageRoles[stage.toLowerCase()] ?? "STAGE";
}
function stageClass(stage: string) {
  const known = stageOrder.includes(stage.toLowerCase()) ? stage.toLowerCase() : "other";
  return `stage-${known}`;
}
function stageScope(stage: string) {
  const present = props.doc.candidate_groups
    .filter((group) => (group.stages[stage] ?? 0) > 0)
    .map((group) => group.index);
  if (!present.length) return "—";
  if (present.length === 1) return `G${present[0]} ONLY`;
  if (present.length === props.doc.candidate_groups.length) return `G0–G${props.doc.candidate_groups.length - 1}`;
  return present.map((index) => `G${index}`).join(" · ");
}
</script>

<template>
  <div class="overview-content">
    <section class="spectrum">
      <div class="sec-head">
        <h2>阶段分布</h2>
        <span>STAGE SPECTRUM · {{ doc.shader_count }} SHADERS</span>
      </div>
      <div class="gates">
        <article v-for="([stage, count]) in stageEntries" :key="stage" class="gate" :class="stageClass(stage)">
          <div class="gate-top">
            <span class="gate-code">{{ stage.toUpperCase() }}</span>
            <span class="gate-role">{{ stageRole(stage) }}</span>
          </div>
          <span class="gate-name">{{ stageLabel(stage) }}</span>
          <div class="gate-value-row"><b class="gate-value">{{ count }}</b><span class="gate-pct">{{ shaderPct(count) }}%</span></div>
          <div class="gate-track"><i :style="{ height: Math.max(shaderPct(count), 1.5) + '%' }" /></div>
          <div class="gate-foot"><span>{{ stageScope(stage) }}</span><span>{{ shaderPct(count) }}%</span></div>
        </article>
      </div>
      <div class="readout">
        <span><b>{{ doc.candidate_groups.length }}</b> GROUPS</span><i class="sep" />
        <span><b>{{ Object.keys(doc.shader_models).length }}</b> SHADER MODEL{{ Object.keys(doc.shader_models).length === 1 ? "" : "S" }} <b>{{ Object.keys(doc.shader_models).join(" / ") }}</b></span><i class="sep" />
        <span class="hash" :class="{ warn: !integrityOk }"><i />{{ integrityOk ? "HASH VERIFIED" : "HASH MISMATCH" }}</span>
      </div>
    </section>

    <section class="group-section">
      <div class="sec-head">
        <h2>分组拓扑</h2>
        <span>GROUP TOPOLOGY · {{ doc.record_count.toLocaleString() }} PASSES</span>
      </div>
      <div class="group-table-wrap">
        <table class="group-table">
          <colgroup><col style="width: 18%" /><col style="width: 14%" /><col style="width: 15%" /><col style="width: 23%" /><col style="width: 30%" /></colgroup>
          <thead><tr><th>GROUP</th><th class="num">PASSES</th><th class="num">UNIQUE</th><th>SHADER MODEL</th><th>STAGE COMPOSITION</th></tr></thead>
          <tbody>
            <tr v-for="group in groups" :key="group.index">
              <td><span class="group-id"><i />GROUP {{ group.index }}</span></td>
              <td class="num">{{ group.record_count }}</td>
              <td class="num">{{ group.unique_shader_count }}</td>
              <td class="model-cell">{{ group.models }}</td>
              <td>
                <div class="stage-composition">
                  <span class="stage-mini" :aria-label="group.stageSummary">
                    <i v-for="([stage, count]) in group.stageParts" :key="stage" :class="stageClass(stage)" :style="{ flexGrow: count }" />
                  </span>
                  <span class="stage-text">{{ group.stageSummary }}</span>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="model-section">
        <div class="model-head"><span>MODEL DISTRIBUTION</span><span>{{ doc.shader_count }} SHADERS</span></div>
        <div class="model-bar" :aria-label="modelEntries.map((entry) => entry.model + ' ' + entry.pct + '%').join('，')">
          <span v-for="entry in modelEntries" :key="entry.model" :class="entry.className" :style="{ flexGrow: entry.count }" :title="`${entry.model} · ${entry.count}`" />
        </div>
        <div class="model-legend">
          <span v-for="entry in modelEntries" :key="entry.model"><i :class="entry.className" /><b>{{ entry.model }}</b><span>{{ entry.count }}</span><em>{{ entry.pct }}%</em></span>
        </div>
        <p class="model-note">{{ modelNote }}</p>
      </div>

      <p class="trace-note"><b>TRACE</b><span>{{ trace }}</span></p>
    </section>
  </div>
</template>
