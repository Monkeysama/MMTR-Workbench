<script setup lang="ts">
import { computed, ref } from "vue";
import { ElButton, ElDialog, ElTag } from "element-plus";
import { ShieldCheck } from "lucide-vue-next";
import type { DocumentInfo } from "../types";

const props = defineProps<{
  modelValue: boolean;
  doc: DocumentInfo | null;
  mdfVersion: number | null;
  mdfGame: string | null;
}>();
const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();
const body = ref<HTMLElement | null>(null);

/** 对话框内容会保留上一次滚动位置；每次打开回到当前文件与图例。 */
function resetScroll() {
  if (body.value) body.value.scrollTop = 0;
}

type Evidence = "game" | "author" | "corpus" | "existing" | "synthetic" | "mmtr";
interface SupportRow {
  id: string;
  name: string;
  games: string[];
  versions: number[];
  format: string;
  mdf: string;
  capability: string;
  evidence: Evidence;
  note: string;
}

const evidence: Record<Evidence, { label: string; type: "success" | "warning" | "info" }> = {
  game: { label: "游戏内已验证", type: "success" },
  author: { label: "作者报告游戏内", type: "warning" },
  corpus: { label: "真实语料", type: "warning" },
  existing: { label: "既有支持", type: "info" },
  synthetic: { label: "仅合成测试", type: "info" },
  mmtr: { label: "仅 MMTR", type: "info" },
};

const rows: SupportRow[] = [
  { id: "mhwilds", name: "MH Wilds", games: ["MHWilds"], versions: [240906211],
    format: "240906211 · 0x110 · 4/5 组", mdf: "2.45", capability: "MMTR / MDF 编辑",
    evidence: "game", note: "真实语料与游戏内验证" },
  { id: "re4", name: "RE4", games: ["RE4"], versions: [221007879],
    format: "221007879 · 0x108 · 3 组", mdf: "2.32", capability: "MMTR / MDF 编辑",
    evidence: "existing", note: "v0.4.2 既有范围，本轮未重验" },
  { id: "onimusha", name: "鬼武者 WotS / Demo", games: ["ONIMUSHAWOTS", "ONIMUSHAWOTSDEMO"], versions: [260605155, 251112814],
    format: "260605155 / 251112814 · 0x110 · per-target", mdf: "2.51 · 104 字节材质项", capability: "MMTR / MDF 编辑、增删、Shader 替换",
    evidence: "author", note: "作者报告 550 个 MMTR、33 个 MDF 通过" },
  { id: "mhrise", name: "MHRISE", games: ["MHRISE"], versions: [220427553],
    format: "220427553 · 0x108 · 3 组", mdf: "2.23", capability: "MMTR / MDF 编辑",
    evidence: "corpus", note: "3 个真实 MMTR 与提取 MDF；未进游戏" },
  { id: "re8", name: "RE8", games: ["RE8"], versions: [2102188797],
    format: "2102188797 · 0x108 · 5 组", mdf: "2.19", capability: "MMTR / MDF 编辑",
    evidence: "synthetic", note: "无本机原版语料" },
  { id: "re2rt", name: "RE2 RT", games: ["RE2RT"], versions: [2109101635],
    format: "2109101635 · 0x108 · 5 组", mdf: "2.21", capability: "MMTR / MDF 编辑",
    evidence: "synthetic", note: "无本机原版语料" },
  { id: "re2", name: "RE2", games: ["RE2"], versions: [1808160001],
    format: "1808160001 · 0x108 · 4 组", mdf: "2.10", capability: "MMTR / MDF 编辑",
    evidence: "synthetic", note: "无本机原版语料" },
  { id: "re3", name: "RE3", games: ["RE3"], versions: [1905100741],
    format: "1905100741 · 0x108 · 4 组", mdf: "2.13", capability: "MMTR / MDF 编辑",
    evidence: "synthetic", note: "无本机原版语料" },
  { id: "sf6", name: "SF6", games: ["SF6"], versions: [221102761, 250109895],
    format: "221102761 / 250109895 · 0x108 · 3 组", mdf: "2.31", capability: "MMTR / MDF 编辑",
    evidence: "synthetic", note: "无本机原版语料" },
  { id: "dd2-old", name: "DD2 旧版", games: ["DD2"], versions: [240112003, 240423143],
    format: "240112003 / 240423143 · 0x108 · 3 组", mdf: "未登记", capability: "仅 MMTR",
    evidence: "synthetic", note: "MDF 未开放" },
  { id: "dd2-current", name: "RE9 / Pragmata / DD2 当前版", games: ["RE9", "PRAGMATA", "DD2"], versions: [251112994, 251112995, 260511270],
    format: "251112994 / 251112995 / 260511270 · 0x110 · per-target", mdf: "2.51 未单独验证", capability: "仅 MMTR",
    evidence: "mmtr", note: "MDF 2.51 目前按鬼武者布局登记" },
];

const currentRow = computed(() => {
  const doc = props.doc;
  if (!doc) return null;
  return rows.find((row) =>
    row.games.includes(doc.game) && row.versions.includes(doc.resource_version)) ?? null;
});
const currentEvidence = computed(() => currentRow.value ? evidence[currentRow.value.evidence] : null);
const mdfLabel = computed(() => props.mdfVersion === null ? null : `MDF 2.${props.mdfVersion}`);
</script>

<template>
  <ElDialog
    :model-value="modelValue"
    width="900px"
    class="support-dialog"
    align-center
    @update:model-value="emit('update:modelValue', $event)"
    @opened="resetScroll"
  >
    <template #header>
      <div class="support-title"><ShieldCheck :size="22" /><div><h2>支持范围</h2><p>MMTR 与 MDF 的已登记格式、可编辑能力与证据等级</p></div></div>
    </template>

    <div ref="body" class="support-body">
      <section v-if="doc" class="support-current">
        <div class="support-current-head">
          <div><span class="support-kicker">当前文件</span><strong>{{ doc.game }} · {{ doc.resource_version }}</strong></div>
          <ElTag :type="currentEvidence?.type ?? 'info'" effect="plain">{{ currentEvidence?.label ?? "未登记版本" }}</ElTag>
        </div>
        <div class="support-current-grid">
          <div><small>记录长度</small><b class="mono">0x{{ doc.record_size.toString(16) }}</b></div>
          <div><small>分组</small><b>{{ doc.candidate_groups.length }} 组 · {{ doc.header_layout === "per-target" ? "每目标" : "统一" }}</b></div>
          <div><small>运行时缓存键</small><b>{{ doc.runtime_cache_keys ? "8 字节" : "无" }}</b></div>
          <div v-if="doc.material_header_bytes"><small>材质头</small><b>{{ doc.material_header_bytes }} 字节</b></div>
        </div>
        <p v-if="mdfLabel" class="support-mdf">{{ mdfLabel }}<span v-if="mdfGame"> · {{ mdfGame }}</span><span v-if="mdfVersion === 51" class="support-warn"> · 2.51 仅鬼武者布局经过验证</span></p>
        <p v-if="currentRow" class="support-current-note">{{ currentRow.note }}</p>
      </section>
      <p v-else class="support-empty">打开 MMTR 文件后，这里会显示当前文件的格式与证据等级。</p>

      <div class="support-legend">
        <span v-for="item in evidence" :key="item.label" class="support-legend-item"><i :class="`dot dot-${item.type}`" />{{ item.label }}</span>
      </div>
      <p class="support-intro">“支持”指工具能按已知布局解析并编辑；它不等于该游戏、材质或 Shader 组合已在游戏内加载通过。未知布局会拒绝操作。</p>

      <div class="support-table-wrap">
        <table class="support-table">
          <thead><tr><th>游戏</th><th>MMTR 格式</th><th>MDF</th><th>能力</th><th>证据</th></tr></thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id" :class="{ 'is-current': currentRow?.id === row.id }">
              <td><b>{{ row.name }}</b><small v-if="currentRow?.id === row.id">当前</small></td>
              <td class="mono">{{ row.format }}</td>
              <td>{{ row.mdf }}</td>
              <td>{{ row.capability }}</td>
              <td><ElTag :type="evidence[row.evidence].type" effect="plain">{{ evidence[row.evidence].label }}</ElTag><small class="support-row-note">{{ row.note }}</small></td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="support-footnote">MDF 2.51 目前统一按鬼武者的 104 字节材质项解析；RE9、Pragmata、DD2 当前版没有单独的真实语料验证。编辑前请保留原文件备份。</p>
    </div>
    <template #footer><ElButton @click="emit('update:modelValue', false)">关闭</ElButton></template>
  </ElDialog>
</template>

<style scoped>
.support-title { display: flex; align-items: flex-start; gap: 12px; color: #3567c8; }
.support-title h2 { margin: 0; color: #29364b; font-size: 21px; }
.support-title p { margin: 4px 0 0; color: #697588; font-size: 14px; }
.support-body { max-height: 70vh; overflow: auto; padding-right: 4px; }
.support-current { border: 1px solid #d7e0ef; background: #f7f9fd; border-radius: 6px; padding: 16px 18px; margin-bottom: 16px; }
.support-current-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.support-current-head strong { display: block; margin-top: 4px; color: #29364b; font-size: 17px; }
.support-kicker { color: #697588; font-size: 13px; }
.support-current-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-top: 14px; }
.support-current-grid small, .support-current-grid b { display: block; }
.support-current-grid small { color: #718097; font-size: 12px; margin-bottom: 3px; }
.support-current-grid b { color: #30343b; font-size: 14px; font-weight: 600; }
.support-mdf, .support-current-note { margin: 12px 0 0; color: #526174; font-size: 13px; line-height: 1.7; }
.support-warn { color: #a15c00; }
.support-empty { color: #697588; font-size: 14px; margin: 0 0 14px; }
.support-legend { display: flex; flex-wrap: wrap; gap: 8px 18px; padding: 10px 0 2px; color: #526174; font-size: 13px; }
.support-legend-item { display: inline-flex; align-items: center; gap: 6px; }
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dot-success { background: #3567c8; }
.dot-warning { background: #c77b16; }
.dot-info { background: #8b95a5; }
.support-intro { color: #697588; font-size: 13px; line-height: 1.8; margin: 8px 0 14px; }
.support-table-wrap { border: 1px solid #e1e5eb; border-radius: 6px; overflow-x: auto; }
.support-table { width: 100%; min-width: 760px; border-collapse: collapse; font-size: 13px; }
.support-table th { text-align: left; font-weight: 600; color: #526174; background: #f5f6f8; padding: 10px 12px; border-bottom: 1px solid #e1e5eb; white-space: nowrap; }
.support-table td { vertical-align: top; padding: 11px 12px; border-bottom: 1px solid #edf0f4; color: #30343b; line-height: 1.6; }
.support-table tr:last-child td { border-bottom: 0; }
.support-table tr.is-current td { background: #edf2fc; }
.support-table td:first-child b { display: block; font-weight: 600; }
.support-table td:first-child small { color: #3567c8; font-size: 12px; }
.support-row-note { display: block; color: #718097; font-size: 12px; line-height: 1.5; margin-top: 4px; max-width: 170px; }
.support-footnote { color: #718097; font-size: 12px; line-height: 1.7; margin: 12px 0 0; }
.mono { font-family: "Cascadia Mono", Consolas, monospace; font-size: 12px; }
@media (max-width: 720px) {
  .support-current-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
