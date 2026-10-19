<script setup lang="ts">
import { computed, shallowRef } from "vue";
import { ElButton, ElInput, ElTable, ElTableColumn, ElTag, ElCheckbox, ElTabs, ElTabPane } from "element-plus";
import { basename, type MdfMigration } from "../../types";
import { mdfValues } from "../../utils/mdfDisplay";
const props = defineProps<{ report: MdfMigration | null; busy: boolean; materialName: string }>();
const emit = defineEmits<{ analyze: []; export: [] }>();
const differencesOnly = shallowRef(false);
const query = shallowRef("");
const section = shallowRef("migration-mapping");
const rows = computed(() => props.report?.property_mapping.filter(row => row.name.toLowerCase().includes(query.value.trim().toLowerCase()) && (!differencesOnly.value || row.status !== "copy_in_place")) ?? []);
const summary = computed(() => Object.entries(props.report?.summary ?? {}).filter(([, count]) => count > 0).map(([status, count]) => ({ status, count, label: label(status) })));
function label(status: string) {
  return ({ copy_in_place: "同名同布局候选", copy_requires_repack: "需要迁移偏移", target_default_required: "需要目标默认值", reserve_value_unspecified: "保留成员值未指定", source_value_missing: "源值缺失", source_binding_mismatch: "源绑定不一致", width_conflict: "成员宽度冲突", source_feature_not_in_target: "源参数无目标入口", mdf_property_not_in_source: "MDF 属性无源声明", source_texture_not_supported: "源纹理无目标入口", source_mdf_slot_not_in_source_mmtr: "纹理无源声明", copy_slot: "同名纹理候选" } as Record<string, string>)[status] ?? status;
}
function statusType(status: string) {
  return status === "copy_in_place" || status === "copy_slot" ? "info" : status === "copy_requires_repack" || status === "reserve_value_unspecified" ? "warning" : "danger";
}
</script>

<template>
  <section class="mdf-feature">
    <div class="mdf-toolbar">
      <ElButton :disabled="busy" type="primary" size="small" @click="emit('analyze')">比较源与目标 MMTR</ElButton>
      <ElButton :disabled="busy || !report" size="small" @click="emit('export')">导出报告</ElButton>
      <span class="secondary-text">仅生成映射报告，不执行迁移</span>
    </div>
    <div v-if="!report" class="mdf-empty">为 {{ materialName }} 选择原 MMTR 和目标模板，查看参数、纹理及布局差异。</div>
    <template v-else>
      <div class="migration-sources">
        <span>源</span><span class="mono" :title="report.source_mmtr.source">{{ basename(report.source_mmtr.source) }}</span>
        <span>目标</span><span class="mono" :title="report.target_mmtr.source">{{ basename(report.target_mmtr.source) }}</span>
      </div>
      <div class="migration-summary" aria-label="迁移差异统计"><ElTag v-for="item in summary" :key="item.status" :type="statusType(item.status)" effect="plain">{{ item.label }} {{ item.count }}</ElTag></div>
      <details class="migration-limits"><summary>尚未验证功能等价，不可自动迁移<span v-if="report.shared_material_count !== undefined"> · {{ report.shared_material_count }} 个材质共用源 master</span></summary><p v-for="item in report.limitations" :key="item">{{ item }}</p></details>
      <ElTabs v-model="section" class="mdf-data-tabs">
        <ElTabPane :label="`参数映射 ${rows.length}`" name="migration-mapping">
          <div class="mapping-content">
            <div class="mdf-toolbar"><ElInput v-model="query" class="mdf-filter" size="small" clearable placeholder="搜索参数名称" aria-label="搜索迁移参数" /><ElCheckbox v-model="differencesOnly">只看布局差异</ElCheckbox><span class="secondary-text">{{ rows.length }} / {{ report.property_mapping.length }}</span></div>
            <ElTable :data="rows" height="100%" stripe>
              <ElTableColumn prop="name" label="参数" min-width="215" show-overflow-tooltip />
              <ElTableColumn label="偏移 源 → 目标" width="128" align="right"><template #default="{ row }"><span :title="`MDF 偏移：${row.mdf_offset ?? '—'}`">{{ row.source_mmtr_offset ?? "—" }} → {{ row.target_mmtr_offset }}</span></template></ElTableColumn>
              <ElTableColumn label="宽度 源 / 目标" width="112" align="right"><template #default="{ row }">{{ row.source_byte_size ?? "—" }} / {{ row.byte_size }}</template></ElTableColumn>
              <ElTableColumn label="源 MDF 值" min-width="140" show-overflow-tooltip><template #default="{ row }"><span class="mono" :title="row.source_values?.join(', ')">{{ mdfValues(row.source_values) }}</span></template></ElTableColumn>
              <ElTableColumn label="状态" min-width="150"><template #default="{ row }"><ElTag :type="statusType(row.status)" :title="report.status_catalog[row.status]" effect="plain">{{ label(row.status) }}</ElTag></template></ElTableColumn>
            </ElTable>
          </div>
        </ElTabPane>
        <ElTabPane :label="`源独有参数 ${report.source_only_properties.length}`" name="migration-source"><ElTable :data="report.source_only_properties" height="100%" stripe><ElTableColumn prop="name" label="参数" min-width="250" /><ElTableColumn prop="source_offset" label="源偏移" width="100" /><ElTableColumn prop="byte_size" label="字节" width="80" /></ElTable></ElTabPane>
        <ElTabPane :label="`纹理映射 ${report.texture_mapping.length}`" name="migration-textures">
          <ElTable :data="report.texture_mapping" height="100%" stripe><ElTableColumn prop="name" label="纹理槽" min-width="210" show-overflow-tooltip /><ElTableColumn label="源路径" min-width="240" show-overflow-tooltip><template #default="{ row }">{{ row.source_paths.join(", ") }}</template></ElTableColumn><ElTableColumn label="状态" min-width="155"><template #default="{ row }"><ElTag :type="statusType(row.status)" :title="report.status_catalog[row.status]" effect="plain">{{ label(row.status) }}</ElTag></template></ElTableColumn></ElTable>
        </ElTabPane>
        <ElTabPane v-if="report.extra_mdf_properties.length" :label="`MDF 额外属性 ${report.extra_mdf_properties.length}`" name="migration-extra"><ElTable :data="report.extra_mdf_properties" height="100%" stripe><ElTableColumn prop="name" label="属性" min-width="250" /><ElTableColumn prop="mdf_offset" label="MDF 偏移" width="100" /><ElTableColumn prop="byte_size" label="字节" width="80" /></ElTable></ElTabPane>
      </ElTabs>
    </template>
  </section>
</template>
<style scoped>
.migration-sources { display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 3px 8px; padding: 7px 10px; font-size: 12px; color: #697588; }
.migration-sources .mono { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.migration-summary { display: flex; gap: 4px 6px; flex-wrap: wrap; padding: 0 10px 6px; }
.migration-limits { flex-shrink: 0; padding: 5px 10px 7px; color: #936216; font-size: 12px; border-bottom: 1px solid #e8ebf0; }
.migration-limits summary { cursor: pointer; }
.migration-limits p { color: #697588; margin: 6px 0 0 12px; }
.mapping-content { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.mapping-content > .el-table { flex: 1; min-height: 0; }
</style>
