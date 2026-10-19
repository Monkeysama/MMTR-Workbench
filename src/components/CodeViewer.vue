<script setup lang="ts">
import { computed, useTemplateRef, watch, nextTick } from "vue";
const props = defineProps<{
  value: string;
  editable?: boolean;
  label: string;
  focusLine?: number;
  selectable?: boolean;
}>();
const emit = defineEmits<{
  update: [value: string];
  selectLine: [line: number];
}>();
const gutter = useTemplateRef<HTMLElement>("gutter");
const scroller = useTemplateRef<HTMLElement>("scroller");
const code = useTemplateRef<HTMLElement>("code");
const highlightTop = computed(
  () => 18 + Math.max(0, (props.focusLine ?? 1) - 1) * 27.2,
);
const lineNumbers = computed(() =>
  Array.from(
    { length: Math.max(1, props.value.split("\n").length) },
    (_, i) => i + 1,
  ).join("\n"),
);
function syncScroll(event: Event) {
  if (gutter.value)
    gutter.value.scrollTop = (event.target as HTMLElement).scrollTop;
}
function selectEditorLine(event: Event) {
  if (!props.selectable) return;
  const editor = event.target as HTMLTextAreaElement;
  emit("selectLine", editor.value.slice(0, editor.selectionStart).split("\n").length);
}
function selectLine(event: MouseEvent) {
  if (!props.selectable || !code.value) return;
  const style = getComputedStyle(code.value),
    rect = code.value.getBoundingClientRect();
  const line =
    Math.floor(
      (event.clientY - rect.top - parseFloat(style.paddingTop)) /
        parseFloat(style.lineHeight),
    ) + 1;
  if (line > 0 && line <= lineNumbers.value.split("\n").length)
    emit("selectLine", line);
}
watch(
  () => [props.focusLine, props.value],
  async () => {
    await nextTick();
    if (!props.focusLine || !scroller.value || !code.value) return;
    const style = getComputedStyle(code.value);
    scroller.value.scrollTop = Math.max(
      0,
      (props.focusLine - 1) * parseFloat(style.lineHeight) -
        scroller.value.clientHeight / 3,
    );
  },
  { flush: "post", immediate: true },
);
</script>
<template>
  <div :class="['code-workspace', { 'code-editable': editable }]">
    <template v-if="editable">
      <pre ref="gutter" class="code-gutter" aria-hidden="true">{{
        lineNumbers
      }}</pre>
      <textarea
        class="source-editor"
        :value="value"
        :aria-label="label"
        spellcheck="false"
        @input="emit('update', ($event.target as HTMLTextAreaElement).value)"
        @scroll="syncScroll"
        @click="selectEditorLine"
        @keyup="selectEditorLine"
        @select="selectEditorLine"
      />
    </template>
    <div v-else ref="scroller" class="code-scroll">
      <div
        v-if="focusLine"
        class="code-line-highlight"
        :style="{ top: highlightTop + 'px' }"
      />
      <pre class="code-gutter" aria-hidden="true">{{ lineNumbers }}</pre>
      <pre
        ref="code"
        class="code-view"
        :class="{ 'code-selectable': selectable }"
        :aria-label="label"
        tabindex="0"
        @click="selectLine"
        >{{ value }}</pre>
    </div>
  </div>
</template>
