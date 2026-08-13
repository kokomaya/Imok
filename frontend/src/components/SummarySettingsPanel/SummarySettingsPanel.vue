<script setup>
/**
 * 会议总结设置面板 — 两个标签页：
 *  1. 总结模板：管理多套自定义模板（章节开关/自定义标题/额外指令/高级完整 Prompt），
 *     并选择当前会议使用哪一个（未配置则用系统默认）。
 *  2. 语言模型：在云端 / 本地 Ollama 等提供商之间切换。
 */

import { ref, computed, onMounted } from 'vue';
import { summaryTemplateStore, SYSTEM_TEMPLATE_ID } from '@/stores/summary-template-store.js';
import { llmProviderStore } from '@/stores/llm-provider-store.js';

const props = defineProps({
  visible: { type: Boolean, default: false },
});
defineEmits(['close']);

const activeTab = ref('template');

const tStore = summaryTemplateStore;
const tState = summaryTemplateStore.state;
const pStore = llmProviderStore;
const pState = llmProviderStore.state;

const editing = computed(() => tStore.activeTemplate.value);
const isSystem = computed(() => tState.selectedId === SYSTEM_TEMPLATE_ID);

function selectTemplate(id) {
  tStore.selectTemplate(id);
}
function addTemplate() {
  tStore.addTemplate('自定义模板');
}
function duplicateCurrent() {
  if (editing.value) tStore.duplicateTemplate(editing.value.id);
}
function removeCurrent() {
  if (editing.value && window.confirm(`确定删除模板“${editing.value.name}”？`)) {
    tStore.removeTemplate(editing.value.id);
  }
}
function onName(e) {
  if (editing.value) tStore.updateTemplate(editing.value.id, { name: e.target.value.trim() || '未命名模板' });
}
function onMode(mode) {
  if (editing.value) tStore.updateTemplate(editing.value.id, { mode });
}
function onSectionToggle(key, e) {
  if (editing.value) tStore.updateSection(editing.value.id, key, { enabled: e.target.checked });
}
function onSectionTitle(key, e) {
  if (editing.value) tStore.updateSection(editing.value.id, key, { title: e.target.value });
}
function onExtra(e) {
  if (editing.value) tStore.updateTemplate(editing.value.id, { extraInstructions: e.target.value });
}
function onAdvSegment(e) {
  if (editing.value) tStore.updateTemplate(editing.value.id, { advancedSegmentSystem: e.target.value });
}
function onAdvMerge(e) {
  if (editing.value) tStore.updateTemplate(editing.value.id, { advancedMergeSystem: e.target.value });
}

// ── 语言模型 ──
function selectProvider(name) {
  pStore.select(name);
}
function refreshProviders() {
  pStore.load();
}

onMounted(() => {
  if (!pState.loaded) pStore.load();
});
</script>

<template>
  <div v-if="visible" class="ssp-backdrop" @click.self="$emit('close')">
  <div class="ssp">
    <div class="ssp-header">
      <span class="ssp-title">⚙ 会议总结设置</span>
      <button class="ssp-close" @click="$emit('close')">✕</button>
    </div>

    <div class="ssp-tabs">
      <button class="ssp-tab" :class="{ active: activeTab === 'template' }" @click="activeTab = 'template'">📝 总结模板</button>
      <button class="ssp-tab" :class="{ active: activeTab === 'model' }" @click="activeTab = 'model'">🧠 语言模型</button>
    </div>

    <!-- ── 总结模板 ── -->
    <div v-if="activeTab === 'template'" class="ssp-body ssp-template">
      <aside class="ssp-list">
        <button
          class="ssp-list-item"
          :class="{ active: isSystem }"
          @click="selectTemplate(SYSTEM_TEMPLATE_ID)"
        >
          <span class="ssp-list-name">系统默认</span>
          <span class="ssp-list-hint">内置模板</span>
        </button>
        <button
          v-for="t in tState.templates"
          :key="t.id"
          class="ssp-list-item"
          :class="{ active: tState.selectedId === t.id }"
          @click="selectTemplate(t.id)"
        >
          <span class="ssp-list-name">{{ t.name }}</span>
          <span class="ssp-list-hint">{{ t.mode === 'advanced' ? '高级' : '章节' }}</span>
        </button>
        <button class="ssp-add" @click="addTemplate">＋ 新建模板</button>
      </aside>

      <section class="ssp-editor">
        <div v-if="isSystem" class="ssp-sys-note">
          <div class="ssp-sys-icon">📋</div>
          <div class="ssp-sys-title">使用系统内置模板</div>
          <p class="ssp-sys-desc">
            当前会议将使用系统自带的总结模板（主题 / 结论 / Action Items / 风险）。<br />
            如需自定义结构或措辞，点击左侧「新建模板」。
          </p>
        </div>

        <template v-else-if="editing">
          <div class="ssp-row ssp-row-name">
            <input class="ssp-input" :value="editing.name" @change="onName" placeholder="模板名称" />
            <div class="ssp-editor-actions">
              <button class="ssp-btn" @click="duplicateCurrent" title="复制此模板">⧉ 复制</button>
              <button class="ssp-btn danger" @click="removeCurrent" title="删除此模板">🗑 删除</button>
            </div>
          </div>

          <div class="ssp-mode">
            <label class="ssp-radio">
              <input type="radio" :checked="editing.mode === 'sections'" @change="onMode('sections')" />
              <span>章节模式（推荐）</span>
            </label>
            <label class="ssp-radio">
              <input type="radio" :checked="editing.mode === 'advanced'" @change="onMode('advanced')" />
              <span>高级（编辑完整 Prompt）</span>
            </label>
          </div>

          <!-- 章节模式 -->
          <div v-if="editing.mode === 'sections'" class="ssp-sections">
            <div class="ssp-section-hint">开关需要输出的章节，并可自定义标题（时间线由段落自动生成）</div>
            <div v-for="sec in editing.sections" :key="sec.key" class="ssp-section-row">
              <label class="ssp-switch">
                <input type="checkbox" :checked="sec.enabled" @change="onSectionToggle(sec.key, $event)" />
              </label>
              <input
                class="ssp-input ssp-section-title"
                :value="sec.title"
                :disabled="!sec.enabled"
                @change="onSectionTitle(sec.key, $event)"
              />
            </div>
            <div class="ssp-field">
              <label class="ssp-label">额外指令（可选）</label>
              <textarea
                class="ssp-textarea"
                rows="3"
                :value="editing.extraInstructions"
                @change="onExtra"
                placeholder="例如：突出风险与阻塞项；用要点式中文输出…"
              ></textarea>
            </div>
          </div>

          <!-- 高级模式 -->
          <div v-else class="ssp-advanced">
            <div class="ssp-warn">
              ⚠ 高级模式下下方已预填【系统默认提示词】，你可在其基础上直接扩展/修改。
              修改只影响当前自定义模板，<strong>不会改动系统默认模板</strong>，随时可切回“系统默认”。
              为保证“主题/结论/待办”标签页正常解析，建议保留 <code>## 主题 / ## 结论 / ## Action Items / ## 风险</code> 这类章节标题。
            </div>
            <div class="ssp-field">
              <label class="ssp-label">段落摘要 System Prompt</label>
              <textarea class="ssp-textarea mono" rows="7" :value="editing.advancedSegmentSystem" @change="onAdvSegment" placeholder="已预填系统默认，可自由扩展"></textarea>
            </div>
            <div class="ssp-field">
              <label class="ssp-label">全局总结 System Prompt</label>
              <textarea class="ssp-textarea mono" rows="7" :value="editing.advancedMergeSystem" @change="onAdvMerge" placeholder="已预填系统默认，可自由扩展"></textarea>
            </div>
          </div>
        </template>
      </section>
    </div>

    <!-- ── 语言模型 ── -->
    <div v-else class="ssp-body ssp-model">
      <div class="ssp-model-head">
        <span class="ssp-model-title">选择当前使用的语言模型提供商</span>
        <button class="ssp-btn" @click="refreshProviders">↺ 刷新</button>
      </div>

      <div v-if="pState.error" class="ssp-warn">⚠ {{ pState.error }}</div>

      <div class="ssp-providers">
        <button
          v-for="p in pState.providers"
          :key="p.name"
          class="ssp-provider"
          :class="{ active: pState.active === p.name }"
          @click="selectProvider(p.name)"
        >
          <div class="ssp-provider-top">
            <span class="ssp-provider-name">{{ p.name }}</span>
            <span class="ssp-badge" :class="p.isLocal ? 'local' : 'cloud'">{{ p.isLocal ? '本地' : '云端' }}</span>
            <span v-if="pState.active === p.name" class="ssp-badge active">✓ 生效中</span>
            <span v-else-if="pState.defaultProvider === p.name" class="ssp-badge">默认</span>
          </div>
          <div class="ssp-provider-meta">
            <span class="ssp-provider-model">{{ p.model }}</span>
            <span class="ssp-provider-url">{{ p.baseUrl }}</span>
          </div>
        </button>
        <div v-if="!pState.providers.length" class="ssp-empty">未找到可用提供商，请检查 config/llm_providers.yaml</div>
      </div>

      <div class="ssp-model-note">
        💡 本地 Ollama：先运行 <code>ollama serve</code> 并 <code>ollama pull &lt;模型&gt;</code>，
        在 <code>config/llm_providers.yaml</code> 的 <code>local_ollama</code> 中把 <code>model</code> 改为对应模型名。
        切换对回看/降级立即生效，对实时会议在下次开始时生效。
      </div>
    </div>
  </div>
  </div>
</template>

<style scoped src="./SummarySettingsPanel.scoped.css"></style>
