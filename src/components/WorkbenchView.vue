<script setup lang="ts">
import { computed, shallowRef, watch } from "vue";
// 品牌角标直接使用发布版本，避免与构建包的版本号脱节。
import { version as appVersion } from "../../package.json";
import {
  ElConfigProvider,
  ElMenu,
  ElMenuItem,
  ElButton,
  ElTag,
  ElTooltip,
  ElAlert,
  ElDrawer,
} from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import {
  Layers3,
  FolderOpen,
  Download,
  LayoutDashboard,
  Workflow,
  Boxes,
  Database,
  Code2,
  Settings2,
  Undo2,
  FileJson,
  ShieldCheck,
  LoaderCircle,
  FileKey,
  Menu,
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
const navigation = shallowRef(false);
// 父容器只读取生命周期摘要；编辑状态与异步回调始终属于当前固定文档。
watch((): DocumentState => ({ source: doc.value?.source ?? null, mdfSource: mdfDoc.value?.source ?? null,
  dirty: wb.dirty.value, busy: busy.value || wb.mdfSaving.value,
  modal: !!dialog.value || settings.value || support.value || navigation.value || wb.materialBatch.visible.value || wb.materialRemoval.visible.value,
}), state => emit("state", state), { immediate: true, flush: "sync" });
const nav: { id: Tab; label: string; icon: typeof Layers3 }[] = [
  { id: "overview", label: "文件概览", icon: LayoutDashboard },
  { id: "passes", label: "渲染通道", icon: Workflow },
  { id: "shaders", label: "着色器", icon: Boxes },
  { id: "metadata", label: "参数与资源", icon: Database },
  { id: "source", label: "HLSL 编辑", icon: Code2 },
  { id: "mdf", label: "MDF 联动", icon: FileKey },
];
const heading = computed(() => nav.find((n) => n.id === tab.value)!);
const descriptions: Record<Tab, string> = {
  overview: "容器结构、阶段分布与完整性检查",
  passes: "查找渲染通道，定位或重定向阶段引用",
  shaders: "筛选编译块，查看反汇编与关联通道",
  metadata: "浏览参数布局、资源名称与缓冲偏移",
  source: "编辑 HLSL，编译并替换选定的着色器",
  mdf: "查看 MDF 材质并校验 MMTR 参数与资源绑定",
};
function navigate(id: string) {
  tab.value = id as Tab;
  navigation.value = false;
}
function unavailable(id: Tab) {
  return (id === "mdf" && !mdfDoc.value) || (!doc.value && !["overview", "source", "mdf"].includes(id));
}
</script>

<template>
  <ElConfigProvider :locale="zhCn">
    <div class="workbench" :aria-busy="busy">
      <aside class="sidebar">
        <div class="brand">
          <Layers3 :size="26" />
          <div><b>MMTR</b><span>Workbench</span></div>
          <span class="version">v{{ appVersion }}</span>
        </div>
        <div class="sidebar-caption">工作空间</div>
        <ElMenu class="workspace-menu" :default-active="tab" @select="navigate">
          <ElMenuItem
            v-for="item in nav"
            :key="item.id"
            :index="item.id"
            :aria-label="item.label"
            :disabled="unavailable(item.id)"
          >
            <component :is="item.icon" :size="19" /><span>{{
              item.label
            }}</span>
            <span
              v-if="doc && item.id === 'shaders'"
              class="menu-count"
              aria-hidden="true"
              >{{ doc.shader_count }}</span
            >
          </ElMenuItem>
        </ElMenu>
        <div class="sidebar-document">
          <div class="sidebar-caption">当前文件</div>
          <template v-if="doc">
            <div class="sidebar-filename">
              <FileJson :size="18" /><b :title="doc.source">{{
                basename(doc.source).split(".mmtr")[0]
              }}</b>
            </div>
            <div class="sidebar-filemeta">
              {{ gameName(doc.game) }} · {{ doc.resource_version ?? "未提供版本" }}<br />{{ bytes(doc.size) }} · {{ doc.shader_count }} 个着色器
            </div>
            <ElTag :type="doc.dirty ? 'warning' : 'info'" effect="plain">{{
              doc.dirty
                ? `${changeCount} 项修改 · ${unsaved ? "待导出" : "已导出"}`
                : "原始文档"
            }}</ElTag>
          </template>
          <p v-else class="sidebar-filemeta">打开 MMTR 文件以开始检查</p>
        </div>
        <div class="sidebar-footer">
          <ElButton text class="settings-button" @click="support = true"
            ><ShieldCheck :size="18" />支持范围</ElButton
          ><ElButton text class="settings-button" @click="settings = true"
            ><Settings2 :size="18" />工具设置</ElButton
          ><span>本地工作空间</span>
        </div>
      </aside>
      <div class="main-shell">
        <header class="topbar">
          <ElButton class="navigation-toggle" text aria-label="页面导航" :aria-expanded="navigation" @click="navigation = true"><Menu :size="20" /></ElButton>
          <DocumentTabs
            :tabs="props.tabs"
            :active-id="props.documentId"
            :active="props.active"
            :disabled="props.workspaceBusy || !!dialog || settings || support || navigation || wb.materialBatch.visible.value || wb.materialRemoval.visible.value"
            @select="emit('select', $event)"
            @close="emit('close', $event)"
            @create="emit('create')"
          />
          <div class="top-actions">
            <ElButton :disabled="props.workspaceBusy" @click="wb.openFile()"
              ><FolderOpen :size="17" />打开 MMTR</ElButton
            ><ElButton :disabled="busy || props.workspaceBusy" @click="wb.openMdf()"
              ><FileKey :size="17" />打开 MDF</ElButton
            ><ElButton
              type="primary"
              :disabled="!doc || busy"
              @click="wb.exportMmtr()"
              ><Download :size="17" />另存导出</ElButton
            >
          </div>
        </header>
        <div class="workspace-sheet">
        <div v-if="doc" class="document-bar">
          <span class="document-path" :title="doc.source">{{
            doc.source
          }}</span>
          <ElTag :type="integrityOk ? 'success' : 'warning'" effect="plain"
            ><ShieldCheck :size="14" />{{
              integrityOk ? "Hash 已验证" : "Hash 存在差异"
            }}</ElTag
          >
          <ElTooltip content="导出修改清单"
            ><ElButton
              text
              :disabled="busy"
              aria-label="修改清单"
              @click="wb.exportData('report')"
              ><FileJson :size="17" /></ElButton
          ></ElTooltip>
          <ElTooltip content="撤销全部修改"
            ><ElButton
              text
              :disabled="busy || !doc.dirty"
              aria-label="撤销全部修改"
              @click="wb.reset()"
              ><Undo2 :size="17" /></ElButton
          ></ElTooltip>
        </div>
        <main class="main-content">
          <div class="page-heading">
            <div>
              <h1>{{ !doc && !mdfDoc && tab === 'overview' ? '材质与着色器工作台' : heading.label }}</h1>
              <p>{{ !doc && !mdfDoc && tab === 'overview' ? '检查 RE Engine 材质，追踪每一个着色阶段。' : descriptions[tab] }}</p>
            </div>
            <ElTag v-if="doc && doc.dirty" type="warning" effect="light"
              >{{ changeCount }} 项修改{{
                unsaved ? "待导出" : "已导出"
              }}</ElTag
            >
          </div>
          <ElAlert
            v-if="error"
            class="page-alert"
            :title="error"
            type="error"
            show-icon
            @close="error = ''"
          />
          <OverviewPanel
            v-if="doc && tab === 'overview'"
            :doc="doc"
            :integrity-ok="integrityOk"
            @navigate="tab = $event"
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
            @export="wb.exportMdf"
            @reset="wb.resetMdf"
            @migrate="wb.analyzeMigration"
            @report="wb.exportMdfReport"
          />
          <section v-else class="welcome">
            <div class="welcome-header">
              <h2>检查 Shader，编辑材质</h2>
              <p>查看渲染通道与着色代码，调整材质参数和贴图。</p>
              <div class="welcome-actions">
                <ElButton type="primary" size="large" :disabled="busy" @click="wb.openFile()"
                  ><FolderOpen :size="19" />打开 MMTR 文件</ElButton
                ><ElButton size="large" :disabled="busy || props.workspaceBusy" @click="wb.openMdf()"
                  ><FileKey :size="18" />打开 MDF 文件</ElButton
                >
              </div>
            </div>
            <dl class="file-relationships">
              <div><dt>MMTR<span>着色器包</span></dt><dd>渲染通道 <span aria-hidden="true">→</span> 编译着色器<br /><span>查看 DXIL、参数布局与资源绑定。</span></dd></div>
              <div><dt>MDF<span>材质配置</span></dt><dd>材质实例 <span aria-hidden="true">→</span> MMTR<br /><span>编辑参数数值、贴图路径与主材质。</span></dd></div>
            </dl>
            <div class="welcome-note">
              <ShieldCheck :size="18" /><span
                >修改暂存在内存中，通过另存导出生成独立文件。</span
              >
              <ElButton text @click="support = true">查看支持游戏</ElButton>
            </div>
          </section>
        </main>
        <footer class="statusbar">
          <span class="status-message"
            ><LoaderCircle v-if="busy" :size="15" class="spin" /><span
              v-else
              class="status-dot"
            />{{ status }}</span
          ><ElButton v-if="!wb.materialBatch.visible.value && (wb.materialBatch.job.value || wb.materialBatch.working.value)"
            text size="small" @click="wb.materialBatch.visible.value = true">查看新增任务</ElButton>
          <ElButton v-if="!wb.materialRemoval.visible.value && (wb.materialRemoval.job.value || wb.materialRemoval.working.value)"
            text size="small" @click="wb.materialRemoval.visible.value = true">查看删除任务</ElButton>
          <span>{{
            doc
              ? `${doc.shader_count} shaders · ${bytes(doc.size)}`
              : "准备就绪"
          }}</span>
        </footer>
        </div>
      </div>
      <ElDrawer v-model="navigation" title="工作空间" direction="ltr" size="260px" class="navigation-drawer">
        <ElMenu class="workspace-menu" :default-active="tab" @select="navigate">
          <ElMenuItem v-for="item in nav" :key="item.id" :index="item.id" :disabled="unavailable(item.id)">
            <component :is="item.icon" :size="18" /><span>{{ item.label }}</span>
          </ElMenuItem>
        </ElMenu>
        <div class="drawer-tools">
          <ElButton text @click="navigation = false; support = true"><ShieldCheck :size="18" />支持范围</ElButton>
          <ElButton text @click="navigation = false; settings = true"><Settings2 :size="18" />工具设置</ElButton>
        </div>
      </ElDrawer>
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
