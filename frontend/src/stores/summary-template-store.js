/**
 * 会议总结模板存储。
 *
 * 单一职责：管理用户自定义总结模板（多套）及当前选择，
 * 持久化到 config/summary_templates.json，并通过 IPC
 * `set_summary_template` 下发给后端（下次/当前会议生效）。
 *
 * 选择 'system' 表示使用系统内置模板（后端收到空字符串即回退默认）。
 */

import { reactive, computed } from 'vue';
import {
  createBlankTemplate,
  normalizeTemplate,
  renderTemplate,
} from '@/components/SummarySettingsPanel/summaryTemplateRender.js';

/** 系统内置模板的特殊选择 ID。 */
export const SYSTEM_TEMPLATE_ID = 'system';

const state = reactive({
  /** @type {Object[]} 用户自定义模板 */
  templates: [],
  /** 当前选择的模板 ID（'system' 或某个自定义模板 id） */
  selectedId: SYSTEM_TEMPLATE_ID,
  loaded: false,
});

/** 当前生效的模板对象（system 或找不到时为 null）。 */
const activeTemplate = computed(() => {
  if (state.selectedId === SYSTEM_TEMPLATE_ID) return null;
  return state.templates.find((t) => t.id === state.selectedId) || null;
});

/** 当前生效模板的显示名称。 */
const activeName = computed(() => {
  return activeTemplate.value ? activeTemplate.value.name : '系统默认';
});

// ── 持久化 ──

function _snapshot() {
  return {
    selectedId: state.selectedId,
    templates: state.templates.map((t) => ({ ...t, sections: t.sections.map((s) => ({ ...s })) })),
  };
}

async function _persist() {
  if (!window.electronAPI?.saveSummaryTemplates) return;
  try {
    await window.electronAPI.saveSummaryTemplates(_snapshot());
  } catch (err) {
    console.error('[summary-template-store] Failed to save:', err);
  }
}

/** 下发当前模板到后端（下次/当前会议生效）。 */
function applyToBackend() {
  if (!window.electronAPI?.sendControl) return;
  const active = activeTemplate.value;
  if (!active) {
    // 系统默认 → 空字符串，后端回退内置模板
    window.electronAPI.sendControl('set_summary_template', {
      segment_system: '',
      merge_system: '',
      section_titles: {},
    });
    return;
  }
  const { segmentSystem, mergeSystem, sectionTitles } = renderTemplate(active);
  window.electronAPI.sendControl('set_summary_template', {
    segment_system: segmentSystem,
    merge_system: mergeSystem,
    section_titles: sectionTitles,
  });
}

/** 供前端回看/降级使用的 prompt（system 返回内置默认）。 */
function frontendPrompts() {
  const { segmentSystem, mergeSystem } = renderTemplate(activeTemplate.value);
  return { summarySystem: segmentSystem, mergeSystem };
}

/** 供前端解析使用的 章节标题→key 映射（用于识别自定义标题）。 */
function sectionTitleMap() {
  const active = activeTemplate.value;
  const map = {};
  if (active && active.mode === 'sections') {
    for (const s of active.sections) {
      if (s.enabled) map[s.title.trim().toLowerCase()] = s.key;
    }
  }
  return map;
}

function _commit() {
  _persist();
  applyToBackend();
}

// ── 生命周期 ──

/** 启动时加载持久化模板并下发后端。 */
async function load() {
  if (window.electronAPI?.getSummaryTemplates) {
    try {
      const result = await window.electronAPI.getSummaryTemplates();
      if (result?.ok && result.settings && typeof result.settings === 'object') {
        const raw = result.settings;
        state.templates = (Array.isArray(raw.templates) ? raw.templates : []).map(normalizeTemplate);
        const sel = raw.selectedId || SYSTEM_TEMPLATE_ID;
        state.selectedId = (sel === SYSTEM_TEMPLATE_ID || state.templates.some((t) => t.id === sel))
          ? sel
          : SYSTEM_TEMPLATE_ID;
      }
    } catch (err) {
      console.error('[summary-template-store] Failed to load:', err);
    }
  }
  state.loaded = true;
  applyToBackend();
}

// ── CRUD ──

function addTemplate(name = '新模板') {
  const tpl = createBlankTemplate(name);
  state.templates.push(tpl);
  state.selectedId = tpl.id;
  _commit();
  return tpl;
}

function duplicateTemplate(id) {
  const src = state.templates.find((t) => t.id === id);
  if (!src) return null;
  const copy = normalizeTemplate({ ...src, name: `${src.name} 副本` });
  copy.id = createBlankTemplate().id;
  state.templates.push(copy);
  state.selectedId = copy.id;
  _commit();
  return copy;
}

function removeTemplate(id) {
  const idx = state.templates.findIndex((t) => t.id === id);
  if (idx === -1) return;
  state.templates.splice(idx, 1);
  if (state.selectedId === id) state.selectedId = SYSTEM_TEMPLATE_ID;
  _commit();
}

/** 更新某个模板的字段（浅合并）。 */
function updateTemplate(id, patch) {
  const tpl = state.templates.find((t) => t.id === id);
  if (!tpl) return;
  Object.assign(tpl, patch);
  _commit();
}

/** 更新某个模板的某个章节。 */
function updateSection(id, key, patch) {
  const tpl = state.templates.find((t) => t.id === id);
  if (!tpl) return;
  const sec = tpl.sections.find((s) => s.key === key);
  if (!sec) return;
  Object.assign(sec, patch);
  _commit();
}

/** 选择当前生效模板（'system' 或某模板 id）。 */
function selectTemplate(id) {
  if (id !== SYSTEM_TEMPLATE_ID && !state.templates.some((t) => t.id === id)) return;
  state.selectedId = id;
  _commit();
}

export const summaryTemplateStore = {
  state,
  activeTemplate,
  activeName,
  SYSTEM_TEMPLATE_ID,
  load,
  applyToBackend,
  frontendPrompts,
  sectionTitleMap,
  addTemplate,
  duplicateTemplate,
  removeTemplate,
  updateTemplate,
  updateSection,
  selectTemplate,
};
