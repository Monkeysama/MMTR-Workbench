<script setup lang="ts">
import { computed, shallowRef, watch, type Component } from "vue";
// 品牌角标直接使用发布版本，避免与构建包的版本号脱节。
import { version as appVersion } from "../../package.json";
import {
  ElConfigProvider,
  ElButton,
  ElTooltip,
  ElAlert,
} from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import {
  Boxes,
  Code2,
  Download,
  FileJson,
  FileKey,
  FolderOpen,
  LayoutDashboard,
  Link2,
  ListTree,
  LoaderCircle,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Undo2,
} from "lucide-vue-next";
import { useWorkbench } from "../composables/useWorkbench";
import { basename, bytes, type Tab, type DocumentTab, type DocumentState, type DocumentController } from "../types";
import OverviewPanel from "./OverviewPanel.vue";
import PassPanel from "./PassPanel.vue";
import ShaderPanel from "./ShaderPanel.vue";
import MetadataPanel from "./MetadataPanel.vue";
import MaterialRemovalDialog from "./material/MaterialRemovalDialog.vue";
import SourcePanel from "./SourcePanel.vue";
import MdfPanel from "./MdfPanel.vue";
import FileDialog from "./FileDialog.vue";
import ToolSettings from "./ToolSettings.vue";
import SupportMatrix from "./SupportMatrix.vue";
import { gameName } from "../gameNames";
import MaterialBatchDialog from "./material/MaterialBatchDialog.vue";
import DocumentTabs from "./DocumentTabs.vue";
import WorkspaceHome from "./WorkspaceHome.vue";
const props = defineProps<{ documentId: string; tabs: DocumentTab[]; active: boolean; workspaceBusy: boolean }>();
const emit = defineEmits<{
  ready: [controller: DocumentController]; state: [state: DocumentState];
  select: [id: string]; close: [id: string]; create: [];
  "open-mmtr": [paths: string[]]; "open-mdf": [path: string];
}>();
const wb = useWorkbench({ documentId: props.documentId,
  openMmtr: paths => emit("open-mmtr", paths), openMdf: path => emit("open-mdf", path) });
emit("ready", { ready: wb.ready, loadMmtr: wb.loadMmtr, loadMdf: wb.loadMdf, confirmClose: wb.confirmClose });
const {
  doc,
  mdfDoc,
  tab,
  busy,
  status,
  error,
  shader,
  ir,
  expert,
  dll,
  source,
  compileResult,
  sourceTrace,
  dialog,
  changeCount,
  unsaved,
  integrityOk,
} = wb;
const settings = shallowRef(false);
const support = shallowRef(false);
// 父容器只读取生命周期摘要；编辑状态与异步回调始终属于当前固定文档。
watch((): DocumentState => ({ source: doc.value?.source ?? null, mdfSource: mdfDoc.value?.source ?? null,
  dirty: wb.dirty.value, busy: busy.value || wb.mdfSaving.value,
  modal: !!dialog.value || settings.value || support.value || wb.materialBatch.visible.value || wb.materialRemoval.visible.value,
}), state => emit("state", state), { immediate: true, flush: "sync" });
const nav: { id: Tab; short: string; label: string; icon: Component }[] = [
  { id: "overview", short: "概览", label: "文件概览", icon: LayoutDashboard },
  { id: "passes", short: "通道", label: "渲染通道", icon: ListTree },
  { id: "shaders", short: "着色器", label: "着色器", icon: Boxes },
  { id: "metadata", short: "参数", label: "参数与资源", icon: SlidersHorizontal },
  { id: "source", short: "源码", label: "HLSL 编辑", icon: Code2 },
  { id: "mdf", short: "材质", label: "材质编辑", icon: Link2 },
];
const heading = computed(() => nav.find((n) => n.id === tab.value)!);
// 页头和另存入口跟随正在查看的文件，编辑与导出仍交给原有控制器。
const mdfActive = computed(() => !!mdfDoc.value && (!doc.value || tab.value === "mdf"));
const exportLabel = computed(() => mdfActive.value ? "另存 MDF" : "另存 MMTR");
const materialCount = computed(() =>
  doc.value?.metadata.filter((m) => m.kind === "member" && m.buffer === "UserMaterial").length ?? 0,
);
const descriptions: Record<Tab, string> = {
  overview: "容器结构、阶段分布与完整性检查",
  passes: "查找渲染通道，定位或重定向阶段引用",
  shaders: "筛选编译块，查看反汇编与关联通道",
  metadata: "浏览参数布局、资源名称与缓冲偏移",
  source: "编辑 HLSL，编译并替换选定的着色器",
  mdf: "编辑材质参数与纹理槽，检查 MMTR 绑定",
};
function navigate(id: Tab) {
  tab.value = id;
}
function unavailable(id: Tab) {
  return (id === "mdf" && !mdfDoc.value) || (!doc.value && !["overview", "source", "mdf"].includes(id));
}
function railMeta(id: Tab) {
  if (id === "overview") return "ALL";
  if (id === "passes") return doc.value ? doc.value.record_count.toLocaleString() : "—";
  if (id === "shaders") return doc.value ? String(doc.value.shader_count) : "—";
  if (id === "metadata") return doc.value ? String(materialCount.value) : "—";
  if (id === "source") return "HLSL";
  return mdfDoc.value ? "MDF" : "—";
}
</script>

<template>
  <ElConfigProvider :locale="zhCn">
    <div class="workbench" :class="{ 'with-inspector': !!doc && !mdfActive }" :aria-busy="busy">
      <header class="topbar">
        <div class="brand">
          <svg class="brand-mark" viewBox="0 0 28 28" aria-hidden="true">
            <path d="M14 2.5 24 8.2v11.6L14 25.5 4 19.8V8.2Z" />
            <circle cx="14" cy="14" r="3.2" />
          </svg>
          <div class="brand-type"><b>MMTR</b><span>WORKBENCH</span></div>
          <span class="version">v{{ appVersion }}</span>
        </div>
        <div v-if="doc || mdfDoc" class="top-status">
          <span class="core-state"><i :class="{ busy }" />本地核心</span>
        </div>
        <div class="top-actions">
          <ElTooltip content="支持范围"><ElButton class="top-icon" text circle aria-label="支持范围" @click="support = true"><ShieldCheck :size="16" /></ElButton></ElTooltip>
          <ElTooltip content="工具设置"><ElButton class="top-icon" text circle aria-label="工具设置" @click="settings = true"><Settings2 :size="16" /></ElButton></ElTooltip>
          <ElButton class="action-button" aria-label="打开 MMTR" title="打开 MMTR" :disabled="props.workspaceBusy" @click="wb.openFile()"><FolderOpen :size="15" /><span class="action-label">打开 MMTR</span></ElButton>
          <ElButton class="action-button" aria-label="打开 MDF" title="打开 MDF" :disabled="busy || props.workspaceBusy" @click="wb.openMdf()"><FileKey :size="15" /><span class="action-label">打开 MDF</span></ElButton>
          <ElButton v-if="doc || mdfDoc" class="action-button action-primary" :aria-label="exportLabel" :title="exportLabel" type="primary" :disabled="busy || wb.mdfSaving.value" @click="mdfActive ? wb.exportMdf() : wb.exportMmtr()"><Download :size="15" /><span class="action-label">{{ exportLabel }}</span></ElButton>
        </div>
      </header>
      <div class="docbar">
        <span class="docbar-label">工作区</span>
        <DocumentTabs
          :tabs="props.tabs"
          :active-id="props.documentId"
          :active="props.active"
          :disabled="props.workspaceBusy || !!dialog || settings || support || wb.materialBatch.visible.value || wb.materialRemoval.visible.value"
          @select="emit('select', $event)"
          @close="emit('close', $event)"
          @create="emit('create')"
        />
        <span class="docbar-count">{{ props.tabs.length }} 个工作区</span>
      </div>
      <div class="shell" :class="{ 'with-inspector': !!doc && !mdfActive }">
        <nav class="rail" aria-label="工作区视图">
          <div class="rail-items">
            <button v-for="item in nav" :key="item.id" type="button" class="rail-item"
              :class="{ 'is-active': tab === item.id }"
              :aria-current="tab === item.id ? 'page' : undefined"
              :aria-label="item.label"
              :disabled="unavailable(item.id)"
              @click="navigate(item.id)">
              <component :is="item.icon" :size="18" :stroke-width="1.7" />
              <span>{{ item.short }}</span>
              <em aria-hidden="true">{{ railMeta(item.id) }}</em>
            </button>
          </div>
        </nav>
        <main class="stage">
          <header v-if="doc && !mdfActive" class="doc-head">
            <div class="doc-meta">
              <span class="doc-tag">SDF / LE</span>
              <span>{{ gameName(doc.game) }}</span>
              <span>{{ bytes(doc.size) }}</span>
              <span class="doc-hash" :class="{ bad: !integrityOk }"><i />{{ integrityOk ? "HASH VERIFIED" : "HASH MISMATCH" }}</span>
            </div>
            <div class="doc-title-row">
              <div class="doc-identity">
                <h1 :title="doc.source">{{ basename(doc.source) }}</h1>
                <p :title="doc.source">{{ doc.source }}</p>
              </div>
            </div>
          </header>
          <header v-else-if="mdfDoc" class="doc-head">
            <div class="doc-meta">
              <span class="doc-tag">MDF 2.{{ mdfDoc.version }}</span>
              <span>{{ gameName(mdfDoc.game) }}</span>
              <span>{{ bytes(mdfDoc.size) }}</span>
              <span>{{ mdfDoc.material_count }} 个材质</span>
            </div>
            <div class="doc-title-row">
              <div class="doc-identity"><h1 :title="mdfDoc.source">{{ basename(mdfDoc.source) }}</h1><p :title="mdfDoc.source">{{ mdfDoc.source }}</p></div>
            </div>
          </header>
          <div class="stage-body">
            <ElAlert v-if="error" class="page-alert" :title="error" type="error" show-icon @close="error = ''" />
            <div v-if="(doc && tab !== 'overview') || (mdfDoc && tab === 'mdf')" class="workspace-head">
              <div><h2>{{ heading.label }}</h2><p>{{ descriptions[tab] }}</p></div>
              <span v-if="!mdfActive">{{ changeCount }} 项修改</span>
            </div>
            <OverviewPanel
              v-if="doc && tab === 'overview'"
              :doc="doc"
              :integrity-ok="integrityOk"
            />
            <PassPanel
              v-else-if="doc && tab === 'passes'"
              :doc="doc"
              :busy="busy"
              @select="wb.selectShader"
              @redirect="wb.redirect"
            />
            <ShaderPanel
              v-else-if="doc && tab === 'shaders'"
              :doc="doc"
              :selected="shader"
              :ir="ir"
              :busy="busy"
              @select="wb.selectShader"
              @export="wb.exportShader"
              @replace="wb.replaceShader"
              @restore="wb.restoreShader"
              @disassemble="wb.disassemble"
              @reconstruct="wb.reconstruct"
            />
            <MetadataPanel
              v-else-if="doc && tab === 'metadata'"
              :doc="doc"
              :busy="busy"
              :batch-enabled="wb.canAddMaterialInterfaces.value"
              :validation="wb.interfaceValidation.value"
              :validating="wb.validatingInterfaces.value"
              @rename="wb.rename"
              @batch="wb.materialBatch.open($event)"
              @remove="wb.deleteMmtrParameter"
              @safe-remove="wb.removeMmtrInterface"
              @add-resource="wb.addMmtrResource"
              @remove-resource="wb.deleteMmtrResource"
              @validate="wb.validateMmtrInterfaces"
              @export="wb.exportData('export_layout')"
            />
            <SourcePanel
              v-else-if="tab === 'source'"
              :source="source"
              :result="compileResult"
              :trace="sourceTrace"
              :shader="shader"
              :busy="busy"
              @update="Object.assign(source, $event)"
              @compile="wb.compile"
              @export="wb.exportCompiled"
              @apply="wb.applyCompiled"
              @sync="wb.syncCompiledInterface"
              @batch-sync="wb.syncCompiledInterfaces"
              @profile="wb.useProfile"
            />
            <MdfPanel
              v-else-if="mdfDoc && tab === 'mdf'"
              :doc="mdfDoc"
              :saving="wb.mdfSaving.value"
              :mmtr-loaded="!!doc"
              :busy="busy"
              @select="wb.selectMdfMaterial"
              @link="wb.linkMdf"
              @edit="wb.editMdfProperty"
              @texture="wb.editMdfTexture"
              @add-property="wb.addMdfProperty"
              @delete-property="wb.deleteMdfProperty"
              @add-texture="wb.addMdfTexture"
              @delete-texture="wb.deleteMdfTexture"
              @setting="wb.editMdfSetting"
              @reset="wb.resetMdf"
              @migrate="wb.analyzeMigration"
              @report="wb.exportMdfReport"
            />
            <WorkspaceHome v-else-if="!doc && !mdfDoc" @source="navigate('source')" @support="support = true" />
            <section v-else-if="!doc && mdfDoc && tab === 'overview'" class="stage-empty">
              <p>MDF 已打开。切换到材质视图查看参数、纹理槽和绑定检查。</p>
              <ElButton @click="navigate('mdf')">打开材质视图</ElButton>
            </section>
            <section v-else-if="doc && tab === 'mdf' && !mdfDoc" class="stage-empty">
              <p>通过顶部打开 MDF，编辑材质参数与纹理槽，并检查当前 MMTR 的绑定。</p>
            </section>
          </div>
        </main>
        <aside v-if="doc && !mdfActive" class="inspector" aria-label="校验与修改">
          <header class="insp-head">
            <h2>校验与修改</h2>
          </header>
          <section class="insp-section verify" :class="{ bad: !integrityOk }">
            <div class="insp-label"><i />{{ integrityOk ? "完整性校验通过" : "完整性校验异常" }}</div>
            <p>{{ integrityOk ? "名称与载荷哈希校验通过。" : "名称或载荷尾项哈希不匹配，导出前需要复核。" }}</p>
            <code :title="doc.sha256">{{ doc.sha256 }}</code>
          </section>
          <section class="insp-section checks">
            <div class="insp-label">检查结果</div>
            <ul>
              <li><span>名称哈希</span><b :class="doc.integrity.name_hash_mismatches.length ? 'bad' : 'ok'">{{ doc.integrity.name_hash_mismatches.length ? doc.integrity.name_hash_mismatches.length + ' 项异常' : "匹配" }}</b></li>
              <li><span>载荷尾项</span><b :class="doc.integrity.shader_trailer_mismatches.length ? 'bad' : 'ok'">{{ doc.integrity.shader_trailer_mismatches.length ? doc.integrity.shader_trailer_mismatches.length + ' 项异常' : "匹配" }}</b></li>
            </ul>
          </section>
          <section class="insp-section tx">
            <div class="insp-label">修改状态</div>
            <dl class="telemetry compact">
              <div><dt>已暂存修改</dt><dd>{{ changeCount }}</dd></div>
              <div><dt>导出状态</dt><dd>{{ unsaved ? "待另存" : "无未保存修改" }}</dd></div>
            </dl>
          </section>
          <div class="insp-actions">
            <ElButton :disabled="busy" @click="wb.exportData('report')"><FileJson :size="14" />导出修改清单</ElButton>
            <button class="insp-reset" type="button" :disabled="busy || !doc.dirty" @click="wb.reset()"><Undo2 :size="13" />撤销全部修改</button>
          </div>
        </aside>
      </div>
      <footer class="statusbar">
        <span class="status-message"><LoaderCircle v-if="busy" :size="14" class="spin" /><span v-else class="status-dot"></span>{{ status }}</span>
        <ElButton v-if="!wb.materialBatch.visible.value && (wb.materialBatch.job.value || wb.materialBatch.working.value)" text size="small" @click="wb.materialBatch.visible.value = true">查看新增任务</ElButton>
        <ElButton v-if="!wb.materialRemoval.visible.value && (wb.materialRemoval.job.value || wb.materialRemoval.working.value)" text size="small" @click="wb.materialRemoval.visible.value = true">查看删除任务</ElButton>
        <span>{{ mdfActive && mdfDoc ? `MDF · ${mdfDoc.material_count} 个材质` : doc ? `${doc.shader_count} 个 Shader · ${bytes(doc.size)}` : "空工作区" }}</span>
      </footer>
      <SupportMatrix
        v-if="props.active"
        v-model="support"
        :doc="doc"
        :mdf-version="mdfDoc?.version ?? null"
        :mdf-game="mdfDoc?.game ?? null"
      />
      <ToolSettings
        v-if="props.active && settings"
        :dll="dll"
        :expert="expert"
        @close="settings = false"
        @dll="wb.saveDll"
        @expert="expert = $event"
      />
      <FileDialog v-if="props.active && dialog" :dialog="dialog" @done="wb.finishDialog" />
      <MaterialBatchDialog v-if="props.active && wb.materialBatch.visible.value"
        v-model:rows="wb.materialBatch.rows.value" v-model:record="wb.materialBatch.record.value"
        v-model:mode="wb.materialBatch.mode.value" v-model:variant="wb.materialBatch.variant.value"
        :passes="doc?.passes ?? []" :plan="wb.materialBatch.plan.value" :job="wb.materialBatch.job.value"
        :error="wb.materialBatch.error.value" :working="wb.materialBatch.working.value"
        :loading="wb.materialBatch.loading.value" :pending="!!wb.materialBatch.pending.value"
        :has-textures="wb.materialBatch.hasTextures.value"
        :notice="wb.materialBatch.notice.value" :candidate="wb.materialBatch.candidate.value"
        @choose="wb.materialBatch.chooseTarget" @export="wb.materialBatch.exportVariant" @discard="wb.materialBatch.discardVariant"
        @save-project="wb.materialBatch.saveProject" @read-project="wb.materialBatch.readProject"
        @minimize="wb.materialBatch.visible.value = false"
        @add="wb.materialBatch.add" @close="wb.materialBatch.close" @preview="wb.materialBatch.preview"
        @start="wb.materialBatch.start" @cancel="wb.materialBatch.cancel" @retry="wb.materialBatch.submitPending" />
      <MaterialRemovalDialog v-if="props.active && wb.materialRemoval.visible.value"
        v-model="wb.materialRemoval.visible.value"
        :action="wb.materialRemoval.action.value" :job="wb.materialRemoval.job.value"
        :eligible="!!wb.materialRemoval.report.value?.eligible"
        :failures="wb.materialRemoval.report.value?.failures ?? []"
        :error="wb.materialRemoval.error.value" :loading="wb.materialRemoval.loading.value"
        :working="wb.materialRemoval.working.value" :pending="!!wb.materialRemoval.pending.value"
        @minimize="wb.materialRemoval.visible.value = false"
        @preview="wb.materialRemoval.preview" @remove="wb.materialRemoval.remove" @cancel="wb.materialRemoval.cancel" @retry="wb.materialRemoval.retry" />
    </div>
  </ElConfigProvider>
</template>
