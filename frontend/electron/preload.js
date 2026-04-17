/**
 * Electron preload 脚本 — 安全 IPC 通道桥接。
 *
 * 单一职责：通过 contextBridge 向渲染进程暴露经过白名单过滤的 IPC 方法。
 * 不包含任何业务逻辑，仅做通道转发。
 *
 * 安全策略：
 *   - contextIsolation: true（主进程配置）
 *   - nodeIntegration: false（主进程配置）
 *   - 仅暴露明确声明的通道，阻止任意 IPC 调用
 */

const { contextBridge, ipcRenderer } = require('electron');

// ---------------------------------------------------------------
// 允许的 IPC 通道白名单
// ---------------------------------------------------------------

/** 允许从 renderer 发往 main 的 invoke 通道 */
const INVOKE_CHANNELS = [
  'python:control',
  'python:start',
  'python:stop',
  'python:status',
  'overlay:open',
  'overlay:close',
  'overlay:set-click-through',
  'overlay:set-always-on-top',
  'mute-panel:toggle',
];

/** 允许从 main 发往 renderer 的 on 通道 */
const RECEIVE_CHANNELS = [
  'python:transcription',
  'python:status',
  'python:error',
  'python:exit',
  'python:restart',
  'python:bridge-error',
  'mute-panel:toggle',
];

// ---------------------------------------------------------------
// 暴露到 window.electronAPI
// ---------------------------------------------------------------

contextBridge.exposeInMainWorld('electronAPI', {
  /**
   * 发送控制命令到 Python 子进程。
   * @param {'start' | 'stop' | 'switch_source'} action
   * @param {Object} [extra]
   * @returns {Promise<{ ok: boolean, error?: string }>}
   */
  sendControl: (action, extra) => {
    return ipcRenderer.invoke('python:control', action, extra);
  },

  /**
   * 启动 Python 子进程。
   * @returns {Promise<{ ok: boolean, error?: string }>}
   */
  startPython: () => {
    return ipcRenderer.invoke('python:start');
  },

  /**
   * 停止 Python 子进程。
   * @returns {Promise<{ ok: boolean }>}
   */
  stopPython: () => {
    return ipcRenderer.invoke('python:stop');
  },

  /**
   * 查询 Python 子进程状态。
   * @returns {Promise<{ running: boolean, pid: number }>}
   */
  getPythonStatus: () => {
    return ipcRenderer.invoke('python:status');
  },

  /**
   * 打开字幕悬浮窗。
   * @returns {Promise<{ ok: boolean }>}
   */
  openOverlay: () => {
    return ipcRenderer.invoke('overlay:open');
  },

  /**
   * 关闭字幕悬浮窗。
   * @returns {Promise<{ ok: boolean }>}
   */
  closeOverlay: () => {
    return ipcRenderer.invoke('overlay:close');
  },

  /**
   * 设置悬浮窗鼠标穿透。
   * @param {boolean} enabled
   * @returns {Promise<{ ok: boolean }>}
   */
  setOverlayClickThrough: (enabled) => {
    return ipcRenderer.invoke('overlay:set-click-through', enabled);
  },

  /**
   * 设置悬浮窗始终置顶。
   * @param {boolean} enabled
   * @returns {Promise<{ ok: boolean }>}
   */
  setOverlayAlwaysOnTop: (enabled) => {
    return ipcRenderer.invoke('overlay:set-always-on-top', enabled);
  },

  /**
   * 切换闭麦面板可见性。
   * @returns {Promise<{ ok: boolean }>}
   */
  toggleMutePanel: () => {
    return ipcRenderer.invoke('mute-panel:toggle');
  },

  /**
   * 注册来自主进程的事件监听。
   * @param {string} channel - 通道名称（必须在白名单中）
   * @param {Function} callback - 回调函数，参数为事件数据
   * @returns {Function} 取消监听函数
   */
  on: (channel, callback) => {
    if (!RECEIVE_CHANNELS.includes(channel)) {
      console.warn(`[preload] Blocked unknown channel: ${channel}`);
      return () => {};
    }

    const handler = (_event, ...args) => callback(...args);
    ipcRenderer.on(channel, handler);

    // 返回取消监听函数
    return () => {
      ipcRenderer.removeListener(channel, handler);
    };
  },

  /**
   * 移除指定通道的所有监听器。
   * @param {string} channel
   */
  removeAllListeners: (channel) => {
    if (RECEIVE_CHANNELS.includes(channel)) {
      ipcRenderer.removeAllListeners(channel);
    }
  },
});
