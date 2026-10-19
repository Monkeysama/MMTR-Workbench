<script setup lang="ts">
import { computed, shallowRef, watch } from "vue";
import { ElButton, ElInput, ElTable, ElTableColumn, ElTabs, ElTabPane, ElTooltip } from "element-plus";
import { Trash2 } from "lucide-vue-next";
import type { MdfMaterial, MdfProperty } from "../../types";
import MdfAutoInput from "./MdfAutoInput.vue";
import MdfPropertyEditor from "./MdfPropertyEditor.vue";
const props = defineProps<{ material: MdfMaterial; busy: boolean; mmtrLoaded: boolean }>();
const emit = defineEmits<{
  link: [];
  edit: [name: string, components: Record<number, number>];
  texture: [slot: number, path: string];
  "add-property": [];
  "delete-property": [name: string];
  "add-texture": [];
  "delete-texture": [slot: number];
}>();
const query = shallowRef("");
const section = shallowRef("input-properties");
const properties = computed(() => props.material.properties.filter(row => row.name.toLowerCase().includes(query.value.trim().toLowerCase())));
const textures = computed(() => props.material.textures.map((row, slot) => ({ ...row, slot })).filter(row => `${row.name} ${row.path}`.toLowerCase().includes(query.value.trim().toLowerCase())));
const trackedMaterial = shallowRef(props.material.index);
const knownProperties = shallowRef(new Set(props.material.properties.map(row => row.name)));
const knownTextures = shallowRef(new Set(props.material.textures.map(row => row.name)));
// 结构新增完成后自动切换页签并用搜索框定位新行；切换材质时只重置基线，不误触发定位。
watch(() => ({
  index: props.material.index,
  properties: props.material.properties.map(row => row.name),
  textures: props.material.textures.map(row => row.name),
}), (next) => {
  if (next.index !== trackedMaterial.value) {
    trackedMaterial.value = next.index;
    knownProperties.value = new Set(next.properties);
    knownTextures.value = new Set(next.textures);
    query.value = "";
    return;
  }
  const addedProperty = next.properties.find(name => !knownProperties.value.has(name));
  const addedTexture = next.textures.find(name => !knownTextures.value.has(name));
  if (addedProperty) {
    section.value = "input-properties";
    query.value = addedProperty;
  } else if (addedTexture) {
    section.value = "input-textures";
    query.value = addedTexture;
  }
  knownProperties.value = new Set(next.properties);
  knownTextures.value = new Set(next.textures);
}, { deep: true });
</script>
<template>
  <section class="mdf-feature mdf-inputs-region">
    <div class="mdf-toolbar">
      <ElInput v-model="query" class="mdf-filter" size="small" clearable placeholder="搜索参数或纹理" aria-label="搜索参数或纹理" />
      <span class="secondary-text">失焦 / Enter 自动暂存 · Esc 取消当前输入</span>
      <ElButton size="small" :disabled="busy" @click="emit('add-property')">新增参数</ElButton>
      <ElButton size="small" :disabled="busy" @click="emit('add-texture')">新增纹理槽</ElButton>
      <ElButton v-if="mmtrLoaded" :disabled="busy" size="small" @click="emit('link')">检查绑定</ElButton>
    </div>
    <ElTabs v-model="section" class="mdf-data-tabs">
      <ElTabPane :label="`参数 ${properties.length} / ${material.property_count}`" name="input-properties">
        <ElTable :data="properties" row-key="name" height="100%" stripe>
          <ElTableColumn prop="name" label="参数" min-width="144" show-overflow-tooltip />
          <ElTableColumn prop="offset" label="偏移" width="50" align="right" />
          <ElTableColumn prop="byte_size" label="字节" width="45" align="right" />
          <ElTableColumn prop="flags" label="标记" width="46" align="right" />
          <ElTableColumn label="值（自动暂存）" min-width="320" fixed="right"><template #default="{ row }"><MdfPropertyEditor :key="`${material.index}:${row.name}`" :property="row as MdfProperty" :busy="busy" @edit="emit('edit', row.name, $event)" /></template></ElTableColumn>
          <ElTableColumn label="操作" width="58" fixed="right"><template #default="{ row }"><ElTooltip content="删除参数"><ElButton text type="danger" size="small" :disabled="busy" :aria-label="`删除参数 ${row.name}`" @click="emit('delete-property', row.name)"><Trash2 :size="16" /></ElButton></ElTooltip></template></ElTableColumn>
        </ElTable>
      </ElTabPane>
      <ElTabPane :label="`纹理 ${textures.length} / ${material.texture_count}`" name="input-textures">
        <ElTable :data="textures" row-key="slot" height="100%" stripe><ElTableColumn prop="name" label="纹理槽" min-width="180" show-overflow-tooltip /><ElTableColumn label="纹理路径（自动暂存）" min-width="350"><template #default="{ row }"><MdfAutoInput :key="`${material.index}:${row.slot}`" :value="row.path" :label="`纹理 ${row.name}`" :busy="busy" @save="emit('texture', row.slot, $event)" /></template></ElTableColumn><ElTableColumn label="操作" width="58"><template #default="{ row }"><ElTooltip content="删除纹理槽"><ElButton text type="danger" size="small" :disabled="busy" :aria-label="`删除纹理槽 ${row.name}`" @click="emit('delete-texture', row.slot)"><Trash2 :size="16" /></ElButton></ElTooltip></template></ElTableColumn></ElTable>
      </ElTabPane>
    </ElTabs>
  </section>
</template>
