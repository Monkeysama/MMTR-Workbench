<script setup lang="ts">
import { computed, watch } from "vue";
import { ElCheckbox, ElColorPicker } from "element-plus";
import type { MdfProperty } from "../../types";
import { mdfEditValues } from "../../utils/mdfDisplay";
import { colorComponents, colorHex, propertyControl } from "../../utils/mdfPropertyControls";
import MdfAutoInput from "./MdfAutoInput.vue";
const props = defineProps<{ property: MdfProperty; busy: boolean }>();
const emit = defineEmits<{ edit: [components: Record<number, number>] }>();
const control = computed(() => propertyControl(props.property));
const labels = computed(() => props.property.values.map((_, index) => control.value === "color" ? ["R", "G", "B", "A"][index]! : props.property.count === 1 ? "值" : ["X", "Y", "Z", "W"][index] ?? String(index + 1)));
let lastPicked: string | undefined;
watch(() => colorHex(props.property.values), value => { if (value !== lastPicked) lastPicked = undefined; });
function pick(value: string | null) {
  if (!value || value === lastPicked) return;
  const components = colorComponents(value);
  if (components) { lastPicked = value; emit("edit", components); }
}
</script>
<template>
  <div class="mdf-property-editor">
    <ElColorPicker v-if="control === 'color'" :model-value="colorHex(property.values)" color-format="hex" :disabled="busy" :aria-label="`颜色 ${property.name}`" popper-class="mdf-color-dropdown" title="选择颜色；数值为线性 RGBA，支持 HDR" @active-change="pick" @change="pick" />
    <ElCheckbox v-else-if="control === 'boolean'" :model-value="property.values[0] !== 0" :disabled="busy" :aria-label="`开关 ${property.name}`" @change="emit('edit', { 0: $event ? 1 : 0 })" />
    <div class="mdf-components" :style="{ gridTemplateColumns: `repeat(${Math.min(property.count, 4)}, minmax(0, 1fr))` }">
      <label v-for="(value, index) in property.values" :key="index" class="mdf-component">
        <span v-if="property.count > 1" :class="{ 'color-channel': control === 'color' }">{{ labels[index] }}</span>
        <MdfAutoInput :value="mdfEditValues([value])" :label="`参数 ${property.name} ${labels[index]}`" :busy="busy" numeric @save="emit('edit', { [index]: Number($event) })" />
      </label>
    </div>
  </div>
</template>
<style scoped>
.mdf-property-editor { display: flex; align-items: center; gap: 6px; min-width: 0; }
.mdf-components { display: grid; gap: 4px; flex: 1; min-width: 0; }
.mdf-component { display: flex; align-items: center; gap: 3px; min-width: 0; }
.mdf-component > span { font-size: 10px; color: var(--muted); flex-shrink: 0; }
.mdf-component > .color-channel { font-weight: 600; color: var(--muted); }
.mdf-property-editor :deep(.el-color-picker) { height: 24px; flex-shrink: 0; }
.mdf-property-editor :deep(.el-color-picker__trigger) { height: 24px; width: 28px; padding: 2px; }
.mdf-property-editor :deep(.el-checkbox) { margin: 0; height: 24px; }
</style>
<style>
/* Color changes are committed while picking; dismiss the popover to finish. */
.mdf-color-dropdown .el-color-picker-panel__footer .el-button { display: none; }
</style>
