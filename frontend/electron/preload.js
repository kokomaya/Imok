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
  'overlay:get-settings',
  'overlay:save-settings',
  'mute-panel:toggle',
  'llm:get-config',
  'llm:chat',
  'llm:chat-stream',
  'meeting:list',
  'meeting:load',
  'meeting:delete',
  'meeting:save-summaries',
  'audio:list-devices',
  'audio:test-device',
  'scenes:list',
  'scenes:save',
  'expression-settings:get',
  'expression-settings:save',
];

/** 允许从 main 发往 renderer 的 on 通道 */
const RECEIVE_CHANNELS = [
  'python:transcription',
  'python:status',
  'python:error',
  'python:exit',
  'python:restart',
  'python:bridge-error',
  'python:segment-summary',
  'python:global-summary',
  'python:audio-level',
  'mute-panel:toggle',
  'menu:action',
  'llm:chat-stream-chunk',
  'llm:chat-stream-done',
  'llm:chat-stream-error',
  'overlay:toggle-lock',
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
   * 获取字幕悬浮窗设置。
   * @returns {Promise<{ ok: boolean, settings: Object }>}
   */
  getOverlaySettings: () => {
    return ipcRenderer.invoke('overlay:get-settings');
  },

  /**
   * 保存字幕悬浮窗设置。
   * @param {Object} settings
   * @returns {Promise<{ ok: boolean }>}
   */
  saveOverlaySettings: (settings) => {
    return ipcRenderer.invoke('overlay:save-settings', settings);
  },

  /**
   * 切换闭麦面板可见性。
   * @returns {Promise<{ ok: boolean }>}
   */
  toggleMutePanel: () => {
    return ipcRenderer.invoke('mute-panel:toggle');
  },

  /**
   * 获取 LLM 配置（从 llm_providers.yaml + .env 读取）。
   * @returns {Promise<{ ok: boolean, config?: Object, error?: string }>}
   */
  getLLMConfig: () => {
    return ipcRenderer.invoke('llm:get-config');
  },

  /**
   * 通过主进程代理 LLM 请求（绕过 CORS）。
   * @param {{ messages: Array, temperature?: number, max_tokens?: number }} params
   * @returns {Promise<{ ok: boolean, content?: string, error?: string }>}
   */
  llmChat: (params) => {
    return ipcRenderer.invoke('llm:chat', params);
  },

  /**
   * 流式 LLM 请求 — 通过 SSE 逐块接收内容。
   * @param {{ messages: Array, temperature?: number, max_tokens?: number }} params
   * @param {{ onChunk?: (delta: string) => void, onDone?: (full: string) => void, onError?: (err: string) => void }} callbacks
   * @returns {Promise<{ ok: boolean, content?: string, error?: string }>}
   */
  llmChatStream: (params, callbacks = {}) => {
    const chunkHandler = (_e, delta) => callbacks.onChunk?.(delta);
    const doneHandler = (_e, full) => callbacks.onDone?.(full);
    const errorHandler = (_e, err) => callbacks.onError?.(err);

    ipcRenderer.on('llm:chat-stream-chunk', chunkHandler);
    ipcRenderer.on('llm:chat-stream-done', doneHandler);
    ipcRenderer.on('llm:chat-stream-error', errorHandler);

    const cleanup = () => {
      ipcRenderer.removeListener('llm:chat-stream-chunk', chunkHandler);
      ipcRenderer.removeListener('llm:chat-stream-done', doneHandler);
      ipcRenderer.removeListener('llm:chat-stream-error', errorHandler);
    };

    return ipcRenderer.invoke('llm:chat-stream', params).finally(cleanup);
  },

  /**
   * 列出所有历史会议。
   * @returns {Promise<{ ok: boolean, meetings?: Array, error?: string }>}
   */
  listMeetings: () => {
    return ipcRenderer.invoke('meeting:list');
  },

  /**
   * 加载指定会议的完整数据。
   * @param {string} meetingId
   * @returns {Promise<{ ok: boolean, data?: Object, error?: string }>}
   */
  loadMeeting: (meetingId) => {
    return ipcRenderer.invoke('meeting:load', meetingId);
  },

  /**
   * 删除指定会议。
   * @param {string} meetingId
   * @returns {Promise<{ ok: boolean, error?: string }>}
   */
  deleteMeeting: (meetingId) => {
    return ipcRenderer.invoke('meeting:delete', meetingId);
  },

  /**
   * 保存会议摘要。
   * @param {string} meetingId
   * @param {Object} summaries - { segments, global_summary, action_items }
   * @returns {Promise<{ ok: boolean, error?: string }>}
   */
  saveMeetingSummaries: (meetingId, summaries) => {
    return ipcRenderer.invoke('meeting:save-summaries', meetingId, summaries);
  },

  // ── 场景管理 ──

  listScenes: () => {
    return ipcRenderer.invoke('scenes:list');
  },

  saveScenes: (scenes) => {
    return ipcRenderer.invoke('scenes:save', scenes);
  },

  // ── 表达设置 ──

  getExpressionSettings: () => {
    return ipcRenderer.invoke('expression-settings:get');
  },

  saveExpressionSettings: (settings) => {
    return ipcRenderer.invoke('expression-settings:save', settings);
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

  /**
   * 同步音频开关状态到主进程菜单勾选。
   * @param {{ systemAudio: boolean, mic: boolean }} state
   */
  syncAudioState: (state) => {
    ipcRenderer.send('menu:audio-state', state);
  },

  /**
   * 列出可用音频设备。
   * @returns {Promise<{ ok: boolean, loopback?: Array, input?: Array, error?: string }>}
   */
  listAudioDevices: () => {
    return ipcRenderer.invoke('audio:list-devices');
  },

  /**
   * 测试音频设备（短录音 + 音量检测）。
   * @param {{ type: 'loopback'|'mic', index: number, seconds?: number }} params
   * @returns {Promise<{ ok: boolean, peak?: number, rms?: number, hasSignal?: boolean, error?: string }>}
   */
  testAudioDevice: (params) => {
    return ipcRenderer.invoke('audio:test-device', params);
  },
});
