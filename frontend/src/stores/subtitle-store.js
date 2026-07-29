/**
 * 字幕数据存储。
 *
 * 单一职责：管理字幕条目的响应式状态。
 * 由 IPC bridge 写入，由 SubtitleOverlay 组件消费。
 */

import { reactive, computed } from 'vue';

/**
 * @typedef {Object} SubtitleEntry
 * @property {number} id - 唯一标识
 * @property {string} original - 原文
 * @property {string} translation - 翻译（可能为空，等待翻译中）
 * @property {string} language - 原文语言 (zh / en / ...)
 * @property {number} timestamp - 时间戳 (epoch ms)
 * @property {'pending' | 'translating' | 'done' | 'error'} translationStatus
 */

const MAX_ENTRIES = 50;
const VISIBLE_COUNT = 5;

let nextId = 1;

const state = reactive({
  /** @type {SubtitleEntry[]} */
  entries: [],

  /** Python 子进程状态 */
  pythonStatus: 'disconnected',

  /** 是否暂停接收 */
  paused: false,
});

/**
 * 添加一条原始转写字幕。
 * @param {{ text: string, language?: string, confidence?: number, segment_start?: number, segment_end?: number }} data
 * @returns {SubtitleEntry}
 */
function addTranscription(data) {
  const entry = {
    id: nextId++,
    original: data.text,
    translation: '',
    language: data.language || '',
    timestamp: Date.now(),
    translationStatus: 'pending',
  };

  state.entries.push(entry);

  // 限制最大条目数
  if (state.entries.length > MAX_ENTRIES) {
    state.entries.splice(0, state.entries.length - MAX_ENTRIES);
  }

  return entry;
}

/**
 * 更新指定条目的翻译文本（支持 streaming 追加）。
 * @param {number} id
 * @param {string} text - 追加的翻译文本片段
 * @param {boolean} [done=false] - 是否翻译完成
 */
function updateTranslation(id, text, done = false) {
  const entry = state.entries.find((e) => e.id === id);
  if (!entry) return;

  entry.translation += text;
  entry.translationStatus = done ? 'done' : 'translating';
}

/**
 * 标记翻译失败。
 * @param {number} id
 */
function markTranslationError(id) {
  const entry = state.entries.find((e) => e.id === id);
  if (entry) {
    entry.translationStatus = 'error';
  }
}

/**
 * 设置 Python 子进程状态。
 * @param {string} status
 */
function setPythonStatus(status) {
  state.pythonStatus = status;
}

/**
 * 清空所有字幕。
 */
function clearAll() {
  state.entries.splice(0, state.entries.length);
}

/**
 * 暂停/恢复接收。
 * @param {boolean} paused
 */
function setPaused(paused) {
  state.paused = paused;
}

// ── 编辑 API（回看模式下手动修正字幕）──

/**
 * 编辑字幕原文。
 * @param {number} id
 * @param {string} newText
 */
function editOriginal(id, newText) {
  const entry = state.entries.find(e => e.id === id);
  if (!entry || entry.original === newText) return;
  entry.original = newText;
}

/**
 * 编辑字幕翻译。
 * @param {number} id
 * @param {string} newText
 */
function editTranslation(id, newText) {
  const entry = state.entries.find(e => e.id === id);
  if (!entry || entry.translation === newText) return;
  entry.translation = newText;
  entry.translationStatus = 'done';
}

/**
 * 获取可序列化的转写数据（用于持久化）。
 * @returns {Object[]}
 */
function getTranscriptionsForSave() {
  return state.entries.map(e => ({
    text: e.original,
    timestamp: e.timestamp,
    language: e.language,
  }));
}

/** 最近的可见字幕（最新 N 条） */
const visibleEntries = computed(() => {
  return state.entries.slice(-VISIBLE_COUNT);
});

export const subtitleStore = {
  state,
  visibleEntries,
  addTranscription,
  updateTranslation,
  markTranslationError,
  setPythonStatus,
  clearAll,
  setPaused,
  editOriginal,
  editTranslation,
  getTranscriptionsForSave,
};
