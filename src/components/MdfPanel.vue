<script setup lang="ts">
import { computed, shallowRef } from "vue";
import { ElButton, ElInput, ElTable, ElTableColumn, ElTabs, ElTabPane, ElTag, ElCheckbox } from "element-plus";
import { basename, bytes, type MdfDocument, type MdfMaterialSummary, type MdfSetting } from "../types";
import MdfMaterialInputs from "./mdf/MdfMaterialInputs.vue";
import MdfBindingPanel from "./mdf/MdfBindingPanel.vue";
import MdfMigrationPanel from "./mdf/MdfMigrationPanel.vue";
import MdfMaterialSettings from "./mdf/MdfMaterialSettings.vue";

const props = defineProps<{ doc: MdfDocument; mmtrLoaded: boolean; busy: boolean; saving: boolean }>();
const emit = defineEmits<{ select: [index: number]; link: []; edit: [name: string, components: Record<number, number>]; texture: [slot: number, path: string]; "add-property": []; "delete-property": [name: string]; "add-texture": []; "delete-texture": [slot: number]; setting: [field: MdfSetting, value: number]; export: []; reset: []; migrate: []; report: [] }>();
const query = shallowRef("");
const onlyLinked = shallowRef(true);
const detailTab = shallowRef(props.doc.migration ? "migration" : "inputs");
const linkageKnown = computed(() => !props.mmtrLoaded || props.doc.materials.some(row => row.mmtr_linked !== undefined));
const materials = computed(() => props.doc.materials.filter(row =>
  (!props.mmtrLoaded || !onlyLinked.value || !linkageKnown.value || row.mmtr_linked === true) &&
  `${row.index} ${row.name} ${row.master}`.toLowerCase().includes(query.value.trim().toLowerCase()),
));
const selectedVisible = computed(() => !!props.doc.selected && (!props.mmtrLoaded || !onlyLinked.value || !linkageKnown.value || props.doc.selected.mmtr_linked === true));
const bindingLabel = (status?: string) => ({ ok: "布局匹配", compatible_with_reserves: "保留成员兼容", review_required: "绑定需检查" }[status ?? ""] ?? "未关联 MMTR");
function inspectBinding() { detailTab.value = "binding"; emit("link"); }
</script>

<template>
  <div class="mdf-workspace">
    <div class="mdf-file-strip">
      <strong :title="doc.source">{{ basename(doc.source) }}</strong>
      <span>MDF 2.{{ doc.version }} · {{ bytes(doc.size) }}</span>
      <span>{{ doc.material_count }} 个材质</span>
      <ElTag v-if="doc.dirty" type="warning" effect="plain">{{ doc.unsaved ? "有未保存修改" : "修改已导出" }}</ElTag>
      <span v-if="saving" class="secondary-text" role="status">正在暂存…</span>
      <div class="mdf-file-actions"><ElButton :disabled="busy || !(doc.dirty || saving)" size="small" @click="emit('reset')">撤销全部修改</ElButton><ElButton :disabled="busy" size="small" type="primary" @click="emit('export')">另存 MDF</ElButton></div>
    </div>
    <div class="mdf-layout">
      <section class="panel mdf-material-list">
        <div class="panel-heading"><h2>材质</h2><span class="secondary-text">{{ materials.length }} / {{ doc.material_count }}</span><ElCheckbox v-model="onlyLinked" :disabled="!mmtrLoaded" size="small">仅显示关联材质</ElCheckbox></div>
        <div class="mdf-search"><ElInput v-model="query" size="small" clearable placeholder="搜索材质或 MMTR" aria-label="搜索材质" /></div>
        <ElTable :data="materials" height="100%" stripe highlight-current-row row-key="index" :current-row-key="doc.selected?.index" @row-click="(row: MdfMaterialSummary) => !busy && emit('select', row.index)">
          <ElTableColumn prop="index" label="#" width="42" />
          <ElTableColumn prop="name" label="材质名称" min-width="150" show-overflow-tooltip />
        </ElTable>
      </section>
      <section v-if="doc.selected && selectedVisible" class="panel mdf-detail">
        <div class="mdf-selected-heading">
          <h2>{{ doc.selected.name }}</h2><span class="secondary-text">#{{ doc.selected.index }} · {{ doc.selected.property_count }} 参数 · {{ doc.selected.texture_count }} 纹理</span>
          <ElTag :type="doc.binding?.status === 'review_required' ? 'warning' : 'info'" effect="plain">{{ bindingLabel(doc.binding?.status) }}</ElTag>
        </div>
        <div class="mdf-master" :title="doc.selected.master"><span>MMTR</span><span class="mono">{{ doc.selected.master }}</span></div>
        <ElTabs v-model="detailTab" class="mdf-detail-tabs">
          <ElTabPane label="材质输入" name="inputs">
            <MdfMaterialInputs :material="doc.selected" :busy="busy" :mmtr-loaded="mmtrLoaded" :can-add-interfaces="mmtrLoaded && doc.selected.mmtr_linked === true" @link="inspectBinding" @edit="(name, components) => emit('edit', name, components)" @texture="(slot, path) => emit('texture', slot, path)" @add-property="emit('add-property')" @delete-property="(name) => emit('delete-property', name)" @add-texture="emit('add-texture')" @delete-texture="(slot) => emit('delete-texture', slot)" />
          </ElTabPane>
          <ElTabPane label="材质设置 · 标记" name="settings">
            <MdfMaterialSettings :key="doc.selected.index" :material="doc.selected" :busy="busy" @setting="(field, value) => emit('setting', field, value)" />
          </ElTabPane>
          <ElTabPane label="绑定检查" name="binding">
            <MdfBindingPanel v-if="doc.binding?.mmtr" :binding="doc.binding" />
            <div v-else class="mdf-empty"><p>打开对应 MMTR 后，检查参数布局和纹理槽。</p><ElButton v-if="mmtrLoaded" size="small" :disabled="busy" @click="emit('link')">检查当前 MMTR 绑定</ElButton></div>
          </ElTabPane>
          <ElTabPane label="模板迁移" name="migration">
            <MdfMigrationPanel :report="doc.migration" :material-name="doc.selected.name" :busy="busy" @analyze="emit('migrate')" @export="emit('report')" />
          </ElTabPane>
        </ElTabs>
      </section>
      <section v-else class="panel mdf-detail mdf-empty-selection">
        <p>当前筛选只显示使用已打开 MMTR 的材质。</p>
        <ElButton size="small" @click="onlyLinked = false">显示全部材质</ElButton>
      </section>
    </div>
  </div>
</template>

<style scoped>
.mdf-workspace { display: flex; flex-direction: column; flex: 1; min-height: 420px; gap: 10px; }
.mdf-file-strip { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 18px; font-size: 13px; color: #697588; }
.mdf-file-actions { display: flex; margin-left: auto; gap: 6px; }
.mdf-file-actions .el-button + .el-button { margin-left: 0; }
.mdf-file-strip strong { color: #3f4c60; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 46%; }
.mdf-layout { display: grid; grid-template-columns: 232px minmax(0, 1fr); gap: 10px; flex: 1; min-height: 0; }
.mdf-material-list, .mdf-detail { display: flex; flex-direction: column; min-height: 0; }
.mdf-workspace :deep(.mdf-feature .el-table .el-table__cell:not(:last-child)) { border-right: 1px solid #dce1e8; }
.mdf-workspace :deep(.mdf-feature .el-table th.el-table__cell) { color: #4c5868; font-weight: 600; }
.mdf-workspace :deep(.mdf-feature .el-table td.el-table__cell:first-child) { color: #273447; font-weight: 500; }
.mdf-workspace :deep(.mdf-feature .el-table td.is-right) { color: #7b8491; font-family: Consolas, monospace; font-size: 12px; }
.mdf-search { padding: 7px 8px; border-bottom: 1px solid #e8ebf0; }
.mdf-material-list > .el-table { flex: 1; min-height: 0; }
.mdf-selected-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; padding: 10px 12px 5px; }
.mdf-selected-heading h2 { font-size: 15px; margin: 0; }
.mdf-selected-heading > .el-tag { margin-left: auto; }
.mdf-master { display: flex; gap: 9px; padding: 0 12px 8px; font-size: 12px; color: #697588; min-width: 0; }
.mdf-master .mono { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.mdf-detail-tabs { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.mdf-detail-tabs > :deep(.el-tabs__header) { margin: 0; padding: 0 12px; flex-shrink: 0; }
.mdf-detail-tabs > :deep(.el-tabs__content) { flex: 1; min-height: 0; }
.mdf-detail-tabs > :deep(.el-tabs__content > .el-tab-pane) { height: 100%; }
.mdf-workspace :deep(.el-tabs__item) { height: 34px; font-size: 13px; }
.mdf-workspace :deep(.el-table) { font-size: 13px; }
.mdf-workspace :deep(.el-table .el-table__cell) { padding: 4px 0; }
.mdf-workspace :deep(.mdf-inputs-region .el-table .el-table__cell) { padding: 3px 0; }
.mdf-workspace :deep(.el-table th.el-table__cell) { height: 32px; }
.mdf-workspace :deep(.el-table .cell) { line-height: 22px; padding: 0 9px; }
.mdf-workspace :deep(.el-table__body tr) { cursor: pointer; }
.mdf-workspace :deep(.el-table .current-row td:first-child) { box-shadow: inset 3px 0 #3567c8; }
.mdf-workspace :deep(.mono) { font-size: 13px; font-variant-numeric: tabular-nums; }
.mdf-workspace :deep(.secondary-text) { font-size: 12px; }
.mdf-workspace :deep(.panel-heading) { padding: 8px 10px; gap: 8px; flex-wrap: wrap; }
.mdf-workspace :deep(.panel-heading h2), .mdf-workspace :deep(.panel-heading h3) { font-size: 14px; margin: 0; }
.mdf-workspace :deep(.el-button--small) { height: 26px; padding: 0 9px; font-size: 12px; }
.mdf-workspace :deep(.el-table .el-button.is-text) { height: 22px; padding: 0 5px; }
.mdf-workspace :deep(.el-tag) { height: 22px; font-size: 12px; }
.mdf-workspace :deep(.el-input) { font-size: 13px; }
.mdf-workspace :deep(.el-checkbox) { height: 26px; }
.mdf-workspace :deep(.el-checkbox__label) { font-size: 12px; }
.mdf-workspace :deep(.mdf-feature) { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.mdf-workspace :deep(.mdf-toolbar) { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 10px; padding: 7px 10px; flex-shrink: 0; border-bottom: 1px solid #e8ebf0; }
.mdf-workspace :deep(.mdf-filter) { width: 215px; }
.mdf-workspace :deep(.mdf-data-tabs) { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.mdf-workspace :deep(.mdf-data-tabs > .el-tabs__header) { margin: 0; padding: 0 10px; background: #fafbfc; }
.mdf-workspace :deep(.mdf-data-tabs > .el-tabs__content) { flex: 1; min-height: 0; }
.mdf-workspace :deep(.mdf-data-tabs > .el-tabs__content > .el-tab-pane) { height: 100%; }
.mdf-workspace :deep(.mdf-note) { margin: 0; padding: 7px 10px; font-size: 12px; color: #697588; border-bottom: 1px solid #e8ebf0; }
.mdf-workspace :deep(.mdf-empty) { padding: 20px; color: #697588; font-size: 13px; }
@media (max-width: 1200px) { .mdf-layout { grid-template-columns: 200px minmax(0, 1fr); } }
</style>
