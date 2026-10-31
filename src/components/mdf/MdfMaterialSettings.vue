<script setup lang="ts">
import { shallowRef } from "vue";
import { ElCheckbox, ElSelect, ElOption } from "element-plus";
import type { MdfMaterial, MdfSetting } from "../../types";
import { shaderTypes, flagFields, flagValue, replaceFlag, type MdfFlagField } from "../../utils/mdfSettings";
import MdfEditableValue from "./MdfEditableValue.vue";
const props = defineProps<{ material: MdfMaterial; busy: boolean }>();
const emit = defineEmits<{ setting: [field: MdfSetting, value: number] }>();
const error = shallowRef("");
function integer(field: MdfSetting, text: string, max = 0x7fffffff, min = -0x80000000) {
  const value = text.trim() ? Number(text) : NaN;
  if (!Number.isInteger(value) || value < min || value > max) { error.value = `请输入 ${min} 到 ${max} 的整数（支持 0x 十六进制）。`; return; }
  error.value = ""; emit("setting", field, value);
}
function bit(lane: number, field: MdfFlagField, value: number) {
  if (!Number.isInteger(value) || value < 0 || value >= 2 ** field.width) { error.value = `${field.name} 需要 0 到 ${2 ** field.width - 1} 的整数。`; return; }
  error.value = "";
  emit("setting", lane === 0 ? "flags_a" : "flags_b", replaceFlag(props.material.flags[lane]!, field, value));
}
</script>
<template>
  <section class="mdf-settings-region">
    <div class="settings-intro">材质设置 · 与 RE Mesh MDF 面板字段对应，修改后另存 MDF。</div>
    <p v-if="error" class="settings-error" role="alert">{{ error }}</p>
    <div class="settings-basics">
      <label>Material Shader Type
        <ElSelect :model-value="material.shader_type" size="small" :disabled="busy" aria-label="Material Shader Type" @update:model-value="emit('setting', 'shader_type', Number($event))">
          <ElOption v-if="!shaderTypes[material.shader_type]" :value="material.shader_type" :label="`Unknown (${material.shader_type})`" />
          <ElOption v-for="(name, value) in shaderTypes" :key="value" :value="value" :label="`${name} (${value})`" />
        </ElSelect>
      </label>
      <label>Shader Type 原始值<MdfEditableValue :value="String(material.shader_type)" label="Shader Type 原始值" :busy="busy" @save="integer('shader_type', $event)" /></label>
      <label>Shader LOD Num<MdfEditableValue :value="String(material.shader_lod)" label="Shader LOD Num" :busy="busy" @save="integer('shader_lod', $event)" /></label>
      <label>Bake Texture Array Size<MdfEditableValue :value="String(material.bake_texture_array_size)" label="Bake Texture Array Size" :busy="busy" @save="integer('bake_texture_array_size', $event)" /></label>
    </div>
    <div class="flags-columns">
      <section v-for="(fields, lane) in flagFields" :key="lane" class="flag-card">
        <h3>标记 {{ lane === 0 ? 'A' : 'B' }} <span>Bit Flag {{ lane === 0 ? 'A' : 'B' }}</span></h3>
        <MdfEditableValue :value="material.flags_hex[lane]!" :label="`Bit Flag ${lane === 0 ? 'A' : 'B'}`" :busy="busy" @save="integer(lane === 0 ? 'flags_a' : 'flags_b', $event, 0xffffffff, 0)" />
        <div class="flag-grid">
          <template v-for="field in fields" :key="field.name">
            <ElCheckbox v-if="field.width === 1" :model-value="!!flagValue(material.flags[lane]!, field)" :disabled="busy" @change="bit(lane, field, $event ? 1 : 0)">{{ field.name }}</ElCheckbox>
            <label v-else class="flag-number">{{ field.name }}<MdfEditableValue :value="String(flagValue(material.flags[lane]!, field))" :label="field.name" :busy="busy" @save="bit(lane, field, $event.trim() ? Number($event) : NaN)" /></label>
          </template>
        </div>
      </section>
    </div>
    <details class="settings-extra">
      <summary>附加数据（只读） · MMTRS {{ material.mmtrs?.length ?? 0 }} 组 · GPBF {{ material.gpbf_name_count }} / {{ material.gpbf_path_count }}</summary>
      <p>Material Name：{{ material.name }} · Name Hash：{{ material.name_hash }} <span v-if="material.name_hash === '0x00000000'">（RE Mesh 的 Hide Material In Game 使用零 hash）</span></p>
      <p class="mono">Master Material Path：{{ material.master }}</p>
      <p v-for="(indices, index) in material.mmtrs" :key="index" class="mono">MMTRS LOD {{ index }}：{{ indices.join(', ') || '空' }}</p>
      <p v-for="(row, index) in material.gpbf" :key="index" class="mono">GPBF {{ index }}：{{ row.name }} → {{ row.path }}</p>
      <p>Linked Material 是 Blender 场景关联；参数的 Float / Bool / Color 控件是插件显示方式，MDF 原始参数仍按 float32 保存。</p>
    </details>
  </section>
</template>
<style scoped>
.mdf-settings-region { height: 100%; overflow: auto; padding: 10px; box-sizing: border-box; font-size: 13px; color: var(--ink); }
.settings-intro { padding: 4px 0 8px; margin-bottom: 10px; border-bottom: 1px solid var(--edge); }
.settings-basics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 16px; padding: 0 0 10px; border-bottom: 1px solid var(--edge); }
.settings-basics > label { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.flags-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 10px; }
.flag-card { padding: 0 10px 0 0; min-width: 0; }
.flag-card + .flag-card { padding: 0 0 0 10px; border-left: 1px solid var(--edge); }
h3 { margin: 0 0 8px; font-size: 13px; } h3 span { font-weight: 400; color: var(--muted); }
.flag-grid { display: flex; flex-direction: column; margin-top: 6px; }
.flag-grid :deep(.el-checkbox) { margin: 0; }
.flag-grid :deep(.el-checkbox__label) { overflow-wrap: anywhere; white-space: normal; font-size: 12px; }
.flag-number { display: grid; grid-template-columns: minmax(0, 1fr) 130px; gap: 6px; align-items: center; margin: 3px 0; }
.settings-extra { padding: 10px 0; margin-top: 10px; border-top: 1px solid var(--edge); overflow-wrap: anywhere; }
.settings-extra summary { cursor: pointer; } .settings-extra p { font-size: 12px; }
.settings-error { color: var(--danger); padding: 6px 0; }
@media (max-width: 1100px) { .flags-columns { grid-template-columns: 1fr; } .flag-card + .flag-card { padding: 10px 0 0; border-left: 0; border-top: 1px solid var(--edge); } }
</style>
