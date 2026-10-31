<script setup lang="ts">
import { computed, shallowRef, watch } from "vue";
import {
  ElSplitter,
  ElSplitterPanel,
  ElInput,
  ElSelect,
  ElOption,
  ElTable,
  ElTableColumn,
  ElButton,
  ElTag,
  ElTabs,
  ElTabPane,
  ElEmpty,
} from "element-plus";
import {
  Search,
  Download,
  Upload,
  Code2,
  Undo2,
  FileCode2,
  ArrowLeft,
} from "lucide-vue-next";
import { hex, bytes, type DocumentInfo, type Shader } from "../types";
import CodeViewer from "./CodeViewer.vue";
import ReadableShaderPanel from "./ReadableShaderPanel.vue";
const props = defineProps<{
  doc: DocumentInfo;
  selected: Shader | null;
  ir: string;
  busy: boolean;
}>();
const emit = defineEmits<{
  select: [index: number];
  export: [];
  replace: [];
  restore: [];
  disassemble: [];
  reconstruct: [];
}>();
const search = shallowRef(""),
  stage = shallowRef("all"),
  detailTab = shallowRef("readable"),
  irFocusLine = shallowRef(0);
const detailOpen = shallowRef(!!props.selected);
const rows = computed(() =>
  props.doc.shaders.filter(
    (s) =>
      (stage.value === "all" || s.stage === stage.value) &&
      `${s.index} ${s.passes.join(" ")}`
        .toLowerCase()
        .includes(search.value.toLowerCase()),
  ),
);
const passRows = computed(
  () => props.selected?.passes.map((name) => ({ name })) ?? [],
);
watch(
  () => props.selected?.index,
  () => {
    detailTab.value = "readable";
    irFocusLine.value = 0;
    detailOpen.value = !!props.selected;
  },
);
function inspect(irLine: number) {
  detailTab.value = "ir";
  irFocusLine.value = irLine;
}
function select(row: Shader) {
  if (!props.busy) {
    detailOpen.value = true;
    emit("select", row.index);
  }
}
</script>
<template>
  <ElSplitter class="shader-layout" :class="{ 'show-shader-detail': detailOpen }">
    <ElSplitterPanel class="shader-master-pane" size="32%" :min="260" :max="540">
      <section class="panel shader-list">
        <div class="shader-filter">
          <ElInput
            v-model="search"
            placeholder="索引或渲染通道…"
            aria-label="搜索着色器"
            clearable
            ><template #prefix><Search :size="17" /></template
          ></ElInput>
          <div class="shader-filter-bottom">
            <ElSelect v-model="stage" aria-label="着色器阶段"
              ><ElOption label="全部阶段" value="all" /><ElOption
                v-for="s in Object.keys(doc.stages)"
                :key="s"
                :label="s.toUpperCase()"
                :value="s" /></ElSelect
            ><span class="secondary-text">{{ rows.length }} 个编译块</span>
          </div>
        </div>
        <ElTable
          :data="rows"
          row-key="index"
          highlight-current-row
          :current-row-key="selected?.index"
          :row-class-name="
            ({ row }) =>
              row.index === selected?.index ? 'selected-shader-row' : ''
          "
          height="100%"
          class="shader-table"
          @row-click="select"
        >
          <ElTableColumn label="编译块" min-width="180"
            ><template #default="{ row }"
              ><div class="shader-cell">
                <ElButton
                  text
                  :disabled="busy"
                  @click.stop="select(row as Shader)"
                  ><FileCode2 :size="17" /><b
                    >Shader {{ String(row.index).padStart(4, "0") }}</b
                  ><i v-if="row.changed" class="change-dot" /></ElButton
                ><span :title="row.passes[0]">{{
                  row.passes[0] || "未引用"
                }}</span>
              </div></template
            ></ElTableColumn
          >
          <ElTableColumn label="阶段" width="78" align="right"
            ><template #default="{ row }"
              ><div class="shader-stage">
                <ElTag type="info" effect="plain">{{
                  row.stage.toUpperCase()
                }}</ElTag
                ><span class="secondary-text">{{ row.model }}</span>
              </div></template
            ></ElTableColumn
          >
        </ElTable>
      </section>
    </ElSplitterPanel>
    <ElSplitterPanel class="shader-detail-pane" :min="340">
      <section class="panel shader-inspector">
        <ElButton class="shader-back" text @click="detailOpen = false"><ArrowLeft :size="16" />返回着色器列表</ElButton>
        <template v-if="selected">
          <div class="inspector-heading">
            <div>
              <h2>Shader #{{ selected.index }}</h2>
              <ElTag effect="plain"
                >{{ selected.stage.toUpperCase() }} ·
                {{ selected.model }}</ElTag
              >
            </div>
            <ElTag v-if="selected.changed" type="warning">已修改</ElTag>
          </div>
          <div class="shader-facts">
            <span
              >偏移 <b class="mono">{{ hex(selected.offset) }}</b></span
            ><span
              >当前 <b>{{ bytes(selected.currentSize) }}</b></span
            ><span
              >原始 <b>{{ bytes(selected.size) }}</b></span
            >
          </div>
          <div class="inspector-toolbar">
            <ElButton
              type="primary"
              :loading="busy"
              @click="
                detailTab = 'readable';
                emit('disassemble');
              "
              ><Code2 :size="17" />反汇编</ElButton
            ><ElButton :disabled="busy || !['ps', 'vs', 'cs', 'ms'].includes(selected.stage)" @click="emit('reconstruct')"
              ><FileCode2 :size="17" />重建 HLSL（实验）</ElButton
            ><ElButton :disabled="busy" @click="emit('export')"
              ><Download :size="17" />导出 DXBC</ElButton
            ><ElButton :disabled="busy" @click="emit('replace')"
              ><Upload :size="17" />导入替换</ElButton
            ><ElButton
              text
              :disabled="busy || !selected.changed"
              @click="emit('restore')"
              ><Undo2 :size="17" />恢复</ElButton
            >
          </div>
          <ElTabs v-model="detailTab" class="inspector-tabs">
            <ElTabPane label="可读伪代码" name="readable">
              <ReadableShaderPanel v-if="ir" :ir="ir" @inspect="inspect" />
              <div v-else class="inspector-empty">
                <ElEmpty description="点击反汇编，生成可读伪代码"
                  ><template #image><Code2 :size="56" /></template></ElEmpty
                ><ElButton
                  :loading="busy"
                  @click="emit('disassemble')"
                  >生成可读代码</ElButton
                >
                <p class="secondary-text">
                  解析参数、贴图与数学运算，保留原始 IR 对照
                </p>
              </div>
            </ElTabPane>
            <ElTabPane label="DXIL 反汇编" name="ir"
              ><CodeViewer
                v-if="ir"
                :value="ir"
                :focus-line="irFocusLine"
                label="DXIL 反汇编"
              />
              <div v-else class="inspector-empty">
                <ElEmpty description="点击反汇编，查看编译块的内部计算"
                  ><template #image><Code2 :size="56" /></template></ElEmpty
                ><span class="secondary-text"
                  >DXIL / LLVM IR · 编译中间代码</span
                >
              </div></ElTabPane
            >
            <ElTabPane :label="`关联通道 (${passRows.length})`" name="passes"
              ><ElTable :data="passRows" height="100%" stripe
                ><ElTableColumn prop="name" label="渲染通道" min-width="260"
                  ><template #default="{ row }"
                    ><span class="mono">{{ row.name }}</span></template
                  ></ElTableColumn
                ></ElTable
              ></ElTabPane
            >
          </ElTabs>
        </template>
        <div v-else class="inspector-empty">
          <ElEmpty description="从左侧列表选择着色器"
            ><template #image><FileCode2 :size="56" /></template></ElEmpty
          ><span class="secondary-text">查看阶段、反汇编和关联渲染通道</span>
        </div>
      </section>
    </ElSplitterPanel>
  </ElSplitter>
</template>
