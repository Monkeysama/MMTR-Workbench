<script setup lang="ts">
import { computed, shallowRef } from "vue";
import { ElInput, ElTable, ElTableColumn, ElTabs, ElTabPane, ElTag, ElCheckbox } from "element-plus";
import type { MdfBinding } from "../../types";
import { basename } from "../../types";
const props = defineProps<{ binding: MdfBinding }>();
const query = shallowRef("");
const differences = shallowRef(false);
const section = shallowRef("binding-properties");
const properties = computed(() => props.binding.properties.filter(row => row.name.toLowerCase().includes(query.value.trim().toLowerCase()) && (!differences.value || row.status !== "match")));
const textures = computed(() => props.binding.textures.filter(row => `${row.name} ${row.path}`.toLowerCase().includes(query.value.trim().toLowerCase()) && (!differences.value || row.status !== "name_in_mmtr_metadata")));
const statusLabel = (status: string) => ({ match: "布局匹配", missing_in_mdf: "MDF 缺少值", offset_or_width_mismatch: "偏移或宽度不同" }[status] ?? status);
const statusType = (status: string) => status === "match" ? "info" : "warning";
</script>
<template>
  <section class="mdf-feature">
    <div class="mdf-toolbar">
      <ElInput v-model="query" class="mdf-filter" size="small" clearable placeholder="搜索绑定成员或纹理" aria-label="搜索绑定成员或纹理" />
      <ElCheckbox v-model="differences">只看差异</ElCheckbox>
      <span class="secondary-text" :title="binding.mmtr?.source">UserMaterial {{ binding.mmtr?.user_material_size ?? 0 }} B · {{ basename(binding.mmtr?.source ?? '') }}</span>
    </div>
    <p class="mdf-note">{{ binding.status === 'compatible_with_reserves' ? '除保留成员外，参数布局一致；纹理名称匹配不代表采样语义相同。' : binding.status === 'review_required' ? '参数或纹理存在差异，请核对偏移、宽度和缺失项。' : '当前结果仅比较元数据布局与纹理名称。' }}<template v-if="binding.missing_mdf_properties.length || binding.missing_mdf_textures.length"> 可新增接口：{{ [...binding.missing_mdf_properties.map(row => row.name), ...binding.missing_mdf_textures.map(row => row.name)].slice(0, 8).join('、') }}<template v-if="binding.missing_mdf_properties.length + binding.missing_mdf_textures.length > 8">等 {{ binding.missing_mdf_properties.length + binding.missing_mdf_textures.length }} 项</template>。</template></p>
    <ElTabs v-model="section" class="mdf-data-tabs">
      <ElTabPane :label="`参数 ${properties.length} / ${binding.properties.length}`" name="binding-properties">
        <ElTable :data="properties" height="100%" stripe>
          <ElTableColumn prop="name" label="成员" min-width="220" show-overflow-tooltip />
          <ElTableColumn prop="mmtr_offset" label="MMTR 偏移" width="100" align="right" />
          <ElTableColumn label="MDF 偏移" width="90" align="right"><template #default="{ row }">{{ row.mdf_offset ?? "—" }}</template></ElTableColumn>
          <ElTableColumn label="宽度 MMTR / MDF" width="140" align="right"><template #default="{ row }">{{ row.mmtr_size }} / {{ row.mdf_size ?? "—" }}</template></ElTableColumn>
          <ElTableColumn label="状态" width="140"><template #default="{ row }"><ElTag :type="statusType(row.status)" effect="plain">{{ statusLabel(row.status) }}</ElTag></template></ElTableColumn>
        </ElTable>
      </ElTabPane>
      <ElTabPane :label="`纹理 ${textures.length} / ${binding.textures.length}`" name="binding-textures">
        <ElTable :data="textures" height="100%" stripe><ElTableColumn prop="name" label="纹理槽" min-width="210" show-overflow-tooltip /><ElTableColumn prop="path" label="路径" min-width="250" show-overflow-tooltip /><ElTableColumn label="MMTR 声明" width="130"><template #default="{ row }"><ElTag :type="row.status === 'name_in_mmtr_metadata' ? 'info' : 'warning'" effect="plain">{{ row.status === 'name_in_mmtr_metadata' ? '有同名资源' : '无同名资源' }}</ElTag></template></ElTableColumn></ElTable>
      </ElTabPane>
      <ElTabPane v-if="binding.extra_mdf_properties.length" :label="`MDF 额外属性 ${binding.extra_mdf_properties.length}`" name="binding-extra">
        <ElTable :data="binding.extra_mdf_properties" height="100%" stripe><ElTableColumn prop="name" label="属性" min-width="220" /><ElTableColumn prop="offset" label="MDF 偏移" width="100" /><ElTableColumn prop="byte_size" label="字节" width="80" /></ElTable>
      </ElTabPane>
    </ElTabs>
  </section>
</template>
