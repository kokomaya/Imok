/**
 * 闭麦辅助面板数据存储。
 *
 * 单一职责：管理闭麦输入/输出的响应式状态。
 * 由 expression-service 写入，由 MuteAssistPanel 组件消费。
 */

import { reactive, computed } from 'vue';

/**
 * @typedef {Object} ExpressionEntry
 * @property {number} id
 * @property {string} input - 中文输入
 * @property {string} output - 英文输出（streaming 追加）
 * @property {'idle' | 'streaming' | 'done' | 'error'} status
 * @property {number} timestamp
 */

const MAX_HISTORY = 20;

let nextId = 1;

const state = reactive({
  /** 当前输入模式 */
  inputMode: 'keyboard', // 'keyboard' | 'mic'

  /** 麦克风状态 */
  micStatus: 'idle', // 'idle' | 'recording' | 'processing'

  /** 当前正在进行的表达请求 ID（null = 空闲） */
  activeId: null,

  /** 当前输入文本 */
  inputText: '',

  /** 当前输出文本（streaming 中） */
  outputText: '',

  /** 当前请求状态 */
  outputStatus: 'idle', // 'idle' | 'streaming' | 'done' | 'error'

  /** 历史记录 */
  /** @type {ExpressionEntry[]} */
  history: [],

  /** 面板是否可见 */
  visible: true,

  /** 已复制提示 */
  copied: false,
});

/**
 * 开始一次新的表达请求。
 * @param {string} input
 * @returns {number} 请求 ID
 */
function startExpression(input) {
  const id = nextId++;
  state.activeId = id;
  state.inputText = input;
  state.outputText = '';
  state.outputStatus = 'streaming';
  state.copied = false;
  return id;
}

/**
 * 追加 streaming 输出。
 * @param {number} id
 * @param {string} chunk
 */
function appendOutput(id, chunk) {
  if (state.activeId !== id) return;
  state.outputText += chunk;
}

/**
 * 标记完成。
 * @param {number} id
 */
function finishExpression(id) {
  if (state.activeId !== id) return;
  state.outputStatus = 'done';

  // 保存到历史
  if (state.inputText.trim() && state.outputText.trim()) {
    state.history.push({
      id,
      input: state.inputText,
      output: state.outputText,
      status: 'done',
      timestamp: Date.now(),
    });
    if (state.history.length > MAX_HISTORY) {
      state.history.splice(0, state.history.length - MAX_HISTORY);
    }
  }
}

/**
 * 标记错误。
 * @param {number} id
 */
function markError(id) {
  if (state.activeId !== id) return;
  state.outputStatus = 'error';
}

/**
 * 设置输入模式。
 * @param {'keyboard' | 'mic'} mode
 */
function setInputMode(mode) {
  state.inputMode = mode;
}

/**
 * 设置麦克风状态。
 * @param {'idle' | 'recording' | 'processing'} status
 */
function setMicStatus(status) {
  state.micStatus = status;
}

/**
 * 切换面板可见性。
 */
function toggleVisible() {
  state.visible = !state.visible;
}

/**
 * 设置面板可见性。
 * @param {boolean} visible
 */
function setVisible(visible) {
  state.visible = visible;
}

/**
 * 标记已复制。
 */
function markCopied() {
  state.copied = true;
  setTimeout(() => {
    state.copied = false;
  }, 2000);
}

/**
 * 重置当前输出（准备下次输入）。
 */
function resetOutput() {
  state.activeId = null;
  state.outputText = '';
  state.outputStatus = 'idle';
  state.copied = false;
}

/**
 * 清空历史。
 */
function clearHistory() {
  state.history.splice(0, state.history.length);
}

/** 是否正在请求中 */
const isStreaming = computed(() => state.outputStatus === 'streaming');

/** 是否有可复制的输出 */
const hasCopyableOutput = computed(
  () => state.outputText.trim().length > 0 && state.outputStatus !== 'streaming',
);

export const muteAssistStore = {
  state,
  isStreaming,
  hasCopyableOutput,
  startExpression,
  appendOutput,
  finishExpression,
  markError,
  setInputMode,
  setMicStatus,
  toggleVisible,
  setVisible,
  markCopied,
  resetOutput,
  clearHistory,
};
