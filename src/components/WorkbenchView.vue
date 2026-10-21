<script setup lang="ts">
import { computed, shallowRef } from "vue";
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
} from "lucide-vue-next";
import { useWorkbench } from "../composables/useWorkbench";
import { basename, bytes, type Tab } from "../types";
import OverviewPanel from "./OverviewPanel.vue";
import PassPanel from "./PassPanel.vue";
import ShaderPanel from "./ShaderPanel.vue";
import MetadataPanel from "./MetadataPanel.vue";
import SourcePanel from "./SourcePanel.vue";
import MdfPanel from "./MdfPanel.vue";
import FileDialog from "./FileDialog.vue";
import ToolSettings from "./ToolSettings.vue";
import FileDropOverlay from "./FileDropOverlay.vue";
import MaterialBatchDialog from "./material/MaterialBatchDialog.vue";
const wb = useWorkbench();
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
const { active: dropActive, title: dropTitle, description: dropDescription, problem: dropProblem } = wb.fileDrop;
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
}
</script>

<template>
  <ElConfigProvider :locale="zhCn">
    <div class="workbench" :aria-busy="busy">
      <FileDropOverlay v-if="dropActive" :title="dropTitle" :description="dropDescription" :invalid="!!dropProblem" />
      <aside class="sidebar">
        <div class="brand">
          <Layers3 :size="26" />
          <div><b>MMTR</b><span>Workbench</span></div>
          <span class="version">{{ appVersion }}</span>
        </div>
        <div class="sidebar-caption">工作空间</div>
        <ElMenu class="workspace-menu" :default-active="tab" @select="navigate">
          <ElMenuItem
            v-for="item in nav"
            :key="item.id"
            :index="item.id"
            :aria-label="item.label"
            :disabled="(item.id === 'mdf' && !mdfDoc) || (!doc && item.id !== 'overview' && item.id !== 'source' && item.id !== 'mdf')"
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
              {{ bytes(doc.size) }} · {{ doc.shader_count }} 个着色器
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
          <ElButton text class="settings-button" @click="settings = true"
            ><Settings2 :size="18" />工具设置</ElButton
          ><span>本地工作空间</span>
        </div>
      </aside>
      <div class="main-shell">
        <header class="topbar">
          <div class="document-tab">
            <FileJson :size="18" /><span :title="doc?.source">{{
              doc ? basename(doc.source) : "MMTR Workbench"
            }}</span
            ><span v-if="unsaved" class="change-dot" />
          </div>
          <div class="top-actions">
            <ElButton :disabled="busy" @click="wb.openFile()"
              ><FolderOpen :size="17" />打开 MMTR</ElButton
            ><ElButton :disabled="busy" @click="wb.openMdf()"
              ><FileKey :size="17" />打开 MDF</ElButton
            ><ElButton
              type="primary"
              :disabled="!doc || busy"
              @click="wb.exportMmtr()"
              ><Download :size="17" />另存导出</ElButton
            >
          </div>
        </header>
        <div v-if="doc" class="document-bar">
          <span class="document-path" :title="doc.source">{{
            doc.source
          }}</span>
          <ElTag :type="integrityOk ? 'primary' : 'warning'" effect="plain"
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
              <h1>{{ heading.label }}</h1>
              <p>{{ descriptions[tab] }}</p>
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
            @rename="wb.rename"
            @add="wb.addMmtrParameter"
            @batch="wb.materialBatch.open()"
            @remove="wb.deleteMmtrParameter"
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
              <span class="welcome-symbol"><Layers3 :size="44" /></span>
              <h2>MMTR Workbench</h2>
              <p>检查、编辑与重建《怪物猎人：荒野》的着色器包</p>
              <p class="secondary-text">将 MMTR 或 MDF 文件拖入窗口即可打开</p>
              <ElButton
                type="primary"
                size="large"
                :disabled="busy"
                @click="wb.openFile()"
                ><FolderOpen :size="19" />打开 MMTR 文件</ElButton
              >
            </div>
            <div class="welcome-grid">
              <div>
                <Workflow :size="24" />
                <h3>追踪渲染通道</h3>
                <p>按分组筛选通道，定位每个阶段的编译块。</p>
              </div>
              <div>
                <Code2 :size="24" />
                <h3>检查着色器</h3>
                <p>查看 DXIL 反汇编，导入 DXBC 或编译 HLSL。</p>
              </div>
              <div>
                <Database :size="24" />
                <h3>浏览材质布局</h3>
                <p>查找参数、资源名称和缓冲区偏移。</p>
              </div>
            </div>
            <div class="welcome-note">
              <ShieldCheck :size="18" /><span
                >编辑在内存中暂存，另存导出时写入独立文件。支持
                .mmtr.250206176。</span
              >
            </div>
          </section>
        </main>
        <footer class="statusbar">
          <span class="status-message"
            ><LoaderCircle v-if="busy" :size="15" class="spin" /><span
              v-else
              class="status-dot"
            />{{ status }}</span
          ><span>{{
            doc
              ? `${doc.shader_count} shaders · ${bytes(doc.size)}`
              : "准备就绪"
          }}</span>
        </footer>
      </div>
      <ToolSettings
        v-if="settings"
        :dll="dll"
        :expert="expert"
        @close="settings = false"
        @dll="wb.saveDll"
        @expert="expert = $event"
      />
      <FileDialog v-if="dialog" :dialog="dialog" @done="wb.finishDialog" />
      <MaterialBatchDialog v-if="wb.materialBatch.visible.value"
        v-model:rows="wb.materialBatch.rows.value" v-model:record="wb.materialBatch.record.value"
        :passes="doc?.passes ?? []" :plan="wb.materialBatch.plan.value" :job="wb.materialBatch.job.value"
        :error="wb.materialBatch.error.value" :working="wb.materialBatch.working.value"
        :loading="wb.materialBatch.loading.value" :pending="!!wb.materialBatch.pending.value"
        :has-textures="wb.materialBatch.hasTextures.value"
        @add="wb.materialBatch.add" @close="wb.materialBatch.close" @preview="wb.materialBatch.preview"
        @start="wb.materialBatch.start" @cancel="wb.materialBatch.cancel" @retry="wb.materialBatch.submitPending" />
    </div>
  </ElConfigProvider>
</template>
