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
 * @property {boolean} [isPartial] - 是否为流式中间结果
 */

const MAX_ENTRIES = 50;
const VISIBLE_COUNT = 5;

let nextId = 1;

/** @type {Map<string, number>} 每个音频源的当前 partial entry id */
const partialEntryIds = new Map();

const state = reactive({
  /** @type {SubtitleEntry[]} */
  entries: [],

  /** Python 子进程状态 */
  pythonStatus: 'disconnected',

  /** 是否暂停接收 */
  paused: false,
});

/**
 * 添加一条原始转写字幕（最终结果），同时移除该源的中间结果条目。
 * @param {{ text: string, language?: string, confidence?: number, segment_start?: number, segment_end?: number, source?: string }} data
 * @returns {SubtitleEntry}
 */
function addTranscription(data) {
  // 移除该音频源的 partial 条目（被最终结果替换）
  const source = data.source || '';
  const partialId = partialEntryIds.get(source);
  if (partialId != null) {
    const idx = state.entries.findIndex((e) => e.id === partialId);
    if (idx !== -1) {
      state.entries.splice(idx, 1);
    }
    partialEntryIds.delete(source);
  }

  const entry = {
    id: nextId++,
    original: data.text,
    translation: '',
    language: data.language || '',
    timestamp: Date.now(),
    translationStatus: 'pending',
    isPartial: false,
  };

  state.entries.push(entry);

  // 限制最大条目数
  if (state.entries.length > MAX_ENTRIES) {
    state.entries.splice(0, state.entries.length - MAX_ENTRIES);
  }

  return entry;
}

/**
 * 更新流式中间转写结果。每个音频源只保留一条 partial 条目，
 * 新的 partial 会替换旧的文本。
 * @param {{ text: string, language?: string, source?: string, segment_start?: number, segment_end?: number }} data
 */
function updatePartial(data) {
  const source = data.source || '';
  const existingId = partialEntryIds.get(source);

  if (existingId != null) {
    // 更新已有的 partial 条目
    const entry = state.entries.find((e) => e.id === existingId);
    if (entry) {
      entry.original = data.text;
      entry.language = data.language || entry.language;
      entry.timestamp = Date.now();
      return;
    }
  }

  // 创建新的 partial 条目
  const entry = {
    id: nextId++,
    original: data.text,
    translation: '',
    language: data.language || '',
    timestamp: Date.now(),
    translationStatus: 'pending',
    isPartial: true,
  };

  state.entries.push(entry);
  partialEntryIds.set(source, entry.id);

  // 限制最大条目数
  if (state.entries.length > MAX_ENTRIES) {
    state.entries.splice(0, state.entries.length - MAX_ENTRIES);
  }
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

/** 最近的可见字幕（最新 N 条） */
const visibleEntries = computed(() => {
  return state.entries.slice(-VISIBLE_COUNT);
});

export const subtitleStore = {
  state,
  visibleEntries,
  addTranscription,
  updatePartial,
  updateTranslation,
  markTranslationError,
  setPythonStatus,
  clearAll,
  setPaused,
};
