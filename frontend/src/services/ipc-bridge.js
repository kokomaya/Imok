/**
 * IPC 桥接服务 — Renderer 侧。
 *
 * 单一职责：监听 Electron IPC 事件，将 Python 子进程消息分发到对应的 store。
 * 不负责翻译逻辑或 UI 渲染。
 *
 * 消息流向：
 *   Python stdout → PythonBridge (main) → IPC → preload → 本服务 → store
 */

import { subtitleStore } from '@/stores/subtitle-store.js';
import { summaryStore } from '@/stores/summary-store.js';

/** @type {Function[]} 清理函数列表 */
let cleanupFns = [];

/** @type {((entry: import('@/stores/subtitle-store.js').SubtitleEntry) => void) | null} */
let onNewTranscription = null;

/**
 * 初始化 IPC 桥接，开始监听主进程转发的 Python 消息。
 * @param {{ onTranscription?: (entry: object) => void }} [options]
 */
function init(options = {}) {
  if (!window.electronAPI) {
    console.warn('[ipc-bridge] Not running in Electron, skipping IPC init');
    return;
  }

  onNewTranscription = options.onTranscription || null;

  // 转写消息 → store
  cleanupFns.push(
    window.electronAPI.on('python:transcription', (data) => {
      if (subtitleStore.state.paused) return;

      const entry = subtitleStore.addTranscription(data);

      // 通知外部（如 LLM 翻译服务）有新转写
      if (onNewTranscription) {
        onNewTranscription(entry);
      }
    }),
  );

  // 流式中间转写 → store（不触发翻译，仅展示）
  cleanupFns.push(
    window.electronAPI.on('python:transcription-partial', (data) => {
      if (subtitleStore.state.paused) return;
      subtitleStore.updatePartial(data);
    }),
  );

  // Python 状态 → store
  cleanupFns.push(
    window.electronAPI.on('python:status', (data) => {
      subtitleStore.setPythonStatus(data.state || 'unknown');
    }),
  );

  // Python 错误
  cleanupFns.push(
    window.electronAPI.on('python:error', (data) => {
      console.error('[ipc-bridge] Python error:', data);
    }),
  );

  // 进程退出
  cleanupFns.push(
    window.electronAPI.on('python:exit', ({ code }) => {
      subtitleStore.setPythonStatus(code === 0 ? 'stopped' : 'crashed');
    }),
  );

  // 重启通知
  cleanupFns.push(
    window.electronAPI.on('python:restart', (info) => {
      console.info('[ipc-bridge] Python restarting:', info);
      subtitleStore.setPythonStatus('restarting');
    }),
  );

  // Bridge 错误
  cleanupFns.push(
    window.electronAPI.on('python:bridge-error', (data) => {
      console.error('[ipc-bridge] Bridge error:', data);
    }),
  );

  // 段落摘要 → summary store
  cleanupFns.push(
    window.electronAPI.on('python:segment-summary', (data) => {
      summaryStore.addSegmentSummary(data);
    }),
  );

  // 全局摘要 → summary store
  cleanupFns.push(
    window.electronAPI.on('python:global-summary', (data) => {
      summaryStore.updateGlobalSummary(data);
    }),
  );
}

/**
 * 发送控制命令到 Python 子进程。
 * @param {'start' | 'stop' | 'switch_source'} action
 * @param {Object} [extra]
 * @returns {Promise<{ ok: boolean, error?: string }>}
 */
async function sendControl(action, extra) {
  if (!window.electronAPI) {
    return { ok: false, error: 'Not in Electron' };
  }
  return window.electronAPI.sendControl(action, extra);
}

/**
 * 启动 Python 子进程。
 */
async function startPython() {
  if (!window.electronAPI) return { ok: false, error: 'Not in Electron' };
  return window.electronAPI.startPython();
}

/**
 * 停止 Python 子进程。
 */
async function stopPython() {
  if (!window.electronAPI) return { ok: false, error: 'Not in Electron' };
  return window.electronAPI.stopPython();
}

/**
 * 销毁所有 IPC 监听器。
 */
function destroy() {
  cleanupFns.forEach((fn) => fn());
  cleanupFns = [];
  onNewTranscription = null;
}

export const ipcBridge = {
  init,
  destroy,
  sendControl,
  startPython,
  stopPython,
};
