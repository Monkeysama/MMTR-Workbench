<script setup lang="ts">
import {
  ElDrawer,
  ElForm,
  ElFormItem,
  ElInput,
  ElSwitch,
  ElAlert,
  ElDivider,
  ElButton,
} from "element-plus";
// 设置抽屉在UI线程转发输入；路径状态和本地持久化由工作台拥有，每次输入必须同步受控值。
defineProps<{ dll: string; expert: boolean }>();
const emit = defineEmits<{
  close: [];
  dll: [value: string];
  expert: [value: boolean];
}>();
</script>
<template>
  <ElDrawer
    :model-value="true"
    title="工具设置"
    size="460px"
    @close="emit('close')"
  >
    <ElForm label-position="top">
      <ElFormItem label="Microsoft DXC 编译器"
        ><ElInput
          :model-value="dll"
          placeholder="dxcompiler.dll 完整路径"
          aria-label="DXC DLL 路径"
          @update:model-value="emit('dll', String($event))"
        />
        <p class="field-help">
          用于 DXIL 反汇编和 HLSL 编译。修改路径后自动保存。
        </p></ElFormItem
      >
      <ElDivider />
      <ElFormItem label="专家选项"
        ><div class="setting-toggle">
          <span>允许接口或资源反射差异</span
          ><ElSwitch
            :model-value="expert"
            aria-label="专家选项"
            @update:model-value="emit('expert', Boolean($event))"
          /></div
      ></ElFormItem>
      <ElAlert
        type="info"
        :closable="false"
        title="资源表不会自动重建"
        description="替换编译块时，仍需保证材质资源、常量布局与输入输出兼容。"
        show-icon
      />
    </ElForm>
    <template #footer
      ><ElButton type="primary" @click="emit('close')">完成</ElButton></template
    >
  </ElDrawer>
</template>
