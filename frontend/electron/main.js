/**
 * Electron 主进程入口。
 *
 * 单一职责：管理应用生命周期和主窗口创建。
 * IPC 通道注册独立为 setupIPC()，PythonBridge 集成独立管理。
 *
 * 开发模式：加载 Vite dev server (http://localhost:5173)
 * 生产模式：加载打包后的 dist/index.html
 */

const { app, BrowserWindow, Menu, ipcMain, globalShortcut, session } = require('electron');
const path = require('path');
const fs = require('fs');
const { PythonBridge } = require('./python-bridge');
const { WindowManager } = require('./window-manager');
const { loadLLMConfig } = require('./llm-config');
const appMenu = require('./app-menu');

// ---------------------------------------------------------------
// 文件日志（生产模式下 console.log 看不到，写文件方便调试）
// ---------------------------------------------------------------

let LOG_FILE = null;
const _origLog = console.log;
const _origWarn = console.warn;
const _origError = console.error;

function _ensureLogFile() {
  if (!LOG_FILE) {
    try {
      LOG_FILE = path.join(app.getPath('userData'), 'main-debug.log');
      fs.writeFileSync(LOG_FILE, `=== Imok main process started ${new Date().toISOString()} ===\n`);
    } catch (_) {
      LOG_FILE = false; // 标记为不可用，不再重试
    }
  }
}

function _writeLog(level, args) {
  _ensureLogFile();
  if (!LOG_FILE) return;
  const ts = new Date().toISOString();
  const msg = args.map(a => (typeof a === 'string' ? a : JSON.stringify(a))).join(' ');
  try {
    fs.appendFileSync(LOG_FILE, `[${ts}] [${level}] ${msg}\n`);
  } catch (_) { /* ignore */ }
}

console.log = (...args) => { _origLog(...args); _writeLog('LOG', args); };
console.warn = (...args) => { _origWarn(...args); _writeLog('WARN', args); };
console.error = (...args) => { _origError(...args); _writeLog('ERROR', args); };

// ---------------------------------------------------------------
// 常量
// ---------------------------------------------------------------

const IS_DEV = !app.isPackaged;
const VITE_DEV_URL = 'http://localhost:5173';
const PRELOAD_PATH = path.join(__dirname, 'preload.js');
const DIST_PATH = path.join(__dirname, '..', 'dist', 'index.html');
const BACKEND_ROOT = IS_DEV
  ? path.resolve(__dirname, '..', '..')
  : path.resolve(process.resourcesPath, 'backend');

// ---------------------------------------------------------------
// Python 路径解析 — 一次性子进程命令用
// ---------------------------------------------------------------

/**
 * 解析 Python 可执行文件路径和运行环境（供 execFile 一次性调用）。
 * 根据打包模式自动选择：PyInstaller exe / 系统 python / venv python。
 *
 * @param {string[]} moduleArgs - Python 模块参数, 如 ['backend.audio.list_devices']
 * @returns {{ pythonPath: string, args: string[], execOpts: object }}
 */
function resolvePythonExec(moduleArgs) {
  if (IS_DEV) {
    return {
      pythonPath: path.resolve(BACKEND_ROOT, '.venv', 'Scripts', 'python'),
      args: ['-m', ...moduleArgs],
      execOpts: {
        cwd: BACKEND_ROOT,
        timeout: 120000,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      },
    };
  }

  const projectRoot = process.resourcesPath;
  const exePath = path.resolve(projectRoot, 'python-backend', 'imok-backend.exe');

  if (fs.existsSync(exePath)) {
    // 完整打包模式：exe --run <module> [args...]
    return {
      pythonPath: exePath,
      args: ['--run', ...moduleArgs],
      execOpts: {
        cwd: projectRoot,
        timeout: 120000,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8', IMOK_PROJECT_ROOT: projectRoot },
      },
    };
  }

  // 轻量模式：系统 python + 源码
  return {
    pythonPath: 'python',
    args: ['-m', ...moduleArgs],
    execOpts: {
      cwd: projectRoot,
      timeout: 120000,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', IMOK_PROJECT_ROOT: projectRoot },
    },
  };
}

// ---------------------------------------------------------------
// 窗口管理
// ---------------------------------------------------------------

/** @type {BrowserWindow | null} */
let mainWindow = null;

/** @type {PythonBridge | null} */
let pythonBridge = null;

/** @type {WindowManager} */
const windowManager = new WindowManager();

/**
 * 创建主窗口。
 */
function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 480,
    height: 720,
    minWidth: 360,
    minHeight: 480,
    title: 'Imok - Meeting Assistant',
    webPreferences: {
      preload: PRELOAD_PATH,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  if (IS_DEV) {
    mainWindow.loadURL(VITE_DEV_URL);
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    mainWindow.loadFile(DIST_PATH);
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ---------------------------------------------------------------
// IPC 通道注册
// ---------------------------------------------------------------

/**
 * 注册 renderer → main 的 IPC 通道。
 * 所有来自渲染进程的请求都通过此处分发。
 */
function setupIPC() {
  // 控制命令：renderer → PythonBridge → Python 子进程
  ipcMain.handle('python:control', (_event, action, extra) => {
    console.log(`[IPC] python:control action=${action} extra=${JSON.stringify(extra)} bridge=${!!pythonBridge} running=${pythonBridge?.isRunning} pid=${pythonBridge?.pid}`);
    if (pythonBridge && pythonBridge.isRunning) {
      pythonBridge.sendControl(action, extra);
      return { ok: true };
    }
    console.warn(`[IPC] python:control REJECTED — bridge not running`);
    return { ok: false, error: 'Python bridge not running' };
  });

  // 启动 Python 子进程
  ipcMain.handle('python:start', () => {
    console.log(`[IPC] python:start bridge=${!!pythonBridge}`);
    if (pythonBridge) {
      pythonBridge.start();
      return { ok: true };
    }
    console.warn('[IPC] python:start REJECTED — bridge not initialized');
    return { ok: false, error: 'Python bridge not initialized' };
  });

  // 停止 Python 子进程
  ipcMain.handle('python:stop', () => {
    console.log(`[IPC] python:stop bridge=${!!pythonBridge}`);
    if (pythonBridge) {
      pythonBridge.destroy();
      pythonBridge = null;
      return { ok: true };
    }
    return { ok: false };
  });

  // 获取 Python 子进程状态
  ipcMain.handle('python:status', () => {
    const s = {
      running: pythonBridge ? pythonBridge.isRunning : false,
      pid: pythonBridge ? pythonBridge.pid : -1,
    };
    console.log(`[IPC] python:status → running=${s.running} pid=${s.pid}`);
    return s;
  });

  // 打开/关闭字幕悬浮窗
  ipcMain.handle('overlay:open', () => {
    windowManager.createOverlayWindow();
    return { ok: true };
  });

  ipcMain.handle('overlay:close', () => {
    windowManager.closeOverlay();
    return { ok: true };
  });

  ipcMain.handle('overlay:set-click-through', (_event, enabled) => {
    windowManager.setClickThrough(enabled);
    return { ok: true };
  });

  ipcMain.handle('overlay:set-always-on-top', (_event, enabled) => {
    windowManager.setAlwaysOnTop(enabled);
    return { ok: true };
  });

  // 字幕设置持久化
  ipcMain.handle('overlay:get-settings', () => {
    return { ok: true, settings: windowManager.loadSettings() };
  });

  ipcMain.handle('overlay:save-settings', (_event, settings) => {
    windowManager.saveSettings(settings);
    return { ok: true };
  });

  // 闭麦面板可见性切换（由快捷键触发，转发到 renderer）
  ipcMain.handle('mute-panel:toggle', () => {
    mainWindow?.webContents.send('mute-panel:toggle');
    return { ok: true };
  });

  // 音频开关状态同步：renderer → main menu checkmarks
  ipcMain.on('menu:audio-state', (_event, state) => {
    appMenu.updateAudioMenuChecks(state);
  });

  // ── 音频设备管理 ─────────────────────────────────────

  // 列出音频设备（一次性 Python 调用）
  ipcMain.handle('audio:list-devices', async () => {
    const { execFile } = require('child_process');
    const { pythonPath, args, execOpts } = resolvePythonExec(['backend.audio.list_devices']);

    return new Promise((resolve) => {
      execFile(
        pythonPath,
        args,
        { ...execOpts },
        (err, stdout, stderr) => {
          if (err) {
            console.error('[audio:list-devices]', stderr || err.message);
            return resolve({ ok: false, error: err.message });
          }
          try {
            const data = JSON.parse(stdout);
            return resolve({ ok: true, ...data });
          } catch (e) {
            return resolve({ ok: false, error: 'Failed to parse device list' });
          }
        },
      );
    });
  });

  // 测试音频设备（短录音 + 音量统计）
  ipcMain.handle('audio:test-device', async (_event, { type, index, seconds }) => {
    const { execFile } = require('child_process');
    const { pythonPath, args: baseArgs, execOpts } = resolvePythonExec([
      'backend.audio.test_device', `--type=${type}`, `--index=${index}`, `--seconds=${seconds || 3}`,
    ]);

    return new Promise((resolve) => {
      execFile(
        pythonPath,
        baseArgs,
        execOpts,
        (err, stdout, stderr) => {
          if (err) {
            console.error('[audio:test-device]', stderr || err.message);
            return resolve({ ok: false, error: err.message });
          }
          try {
            return resolve(JSON.parse(stdout));
          } catch (e) {
            return resolve({ ok: false, error: 'Failed to parse test result' });
          }
        },
      );
    });
  });

  // LLM 配置：读取 llm_providers.yaml + .env → 返回给 renderer
  ipcMain.handle('llm:get-config', () => {
    return loadLLMConfig(BACKEND_ROOT);
  });

  // LLM 请求代理：renderer → main → VIO API（绕过 CORS）
  ipcMain.handle('llm:chat', async (_event, { messages, temperature, max_tokens }) => {
    const { net } = require('electron');
    const result = loadLLMConfig(BACKEND_ROOT);
    if (!result.ok) return { ok: false, error: result.error };

    const cfg = result.config;
    const url = `${cfg.baseUrl.replace(/\/+$/, '')}/chat/completions`;

    const headers = {
      'Content-Type': 'application/json',
      ...(cfg.apiKey ? { Authorization: `Bearer ${cfg.apiKey}` } : {}),
      ...(cfg.headers || {}),
    };

    const body = JSON.stringify({
      model: cfg.model,
      messages,
      stream: false,
      temperature: temperature ?? 0.3,
      max_tokens: max_tokens ?? 512,
    });

    try {
      const response = await net.fetch(url, { method: 'POST', headers, body });
      if (!response.ok) {
        const text = await response.text();
        return { ok: false, error: `HTTP ${response.status}: ${text}` };
      }
      const data = await response.json();
      const content = data.choices?.[0]?.message?.content || '';
      return { ok: true, content };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  // LLM 流式请求代理：通过 SSE 逐块推送到 renderer
  ipcMain.handle('llm:chat-stream', async (event, { messages, temperature, max_tokens }) => {
    const { net } = require('electron');
    const result = loadLLMConfig(BACKEND_ROOT);
    if (!result.ok) {
      event.sender.send('llm:chat-stream-error', result.error);
      return { ok: false, error: result.error };
    }

    const cfg = result.config;
    const url = `${cfg.baseUrl.replace(/\/+$/, '')}/chat/completions`;

    const headers = {
      'Content-Type': 'application/json',
      ...(cfg.apiKey ? { Authorization: `Bearer ${cfg.apiKey}` } : {}),
      ...(cfg.headers || {}),
    };

    const body = JSON.stringify({
      model: cfg.model,
      messages,
      stream: true,
      temperature: temperature ?? 0.3,
      max_tokens: max_tokens ?? 512,
    });

    try {
      const response = await net.fetch(url, { method: 'POST', headers, body });
      if (!response.ok) {
        const text = await response.text();
        const error = `HTTP ${response.status}: ${text}`;
        event.sender.send('llm:chat-stream-error', error);
        return { ok: false, error };
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let fullContent = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith('data:')) continue;
          const payload = trimmed.slice(5).trim();
          if (payload === '[DONE]') continue;

          try {
            const json = JSON.parse(payload);
            const delta = json.choices?.[0]?.delta?.content || '';
            if (delta) {
              fullContent += delta;
              event.sender.send('llm:chat-stream-chunk', delta);
            }
          } catch (_) { /* skip malformed SSE lines */ }
        }
      }

      event.sender.send('llm:chat-stream-done', fullContent);
      return { ok: true, content: fullContent };
    } catch (err) {
      event.sender.send('llm:chat-stream-error', err.message);
      return { ok: false, error: err.message };
    }
  });

  // ── 会议历史管理 ──────────────────────────────────────────

  const meetingsDir = path.join(BACKEND_ROOT, 'data', 'meetings');

  // 列出所有会议
  ipcMain.handle('meeting:list', () => {
    try {
      if (!fs.existsSync(meetingsDir)) return { ok: true, meetings: [] };
      const dirs = fs.readdirSync(meetingsDir, { withFileTypes: true });
      const meetings = [];
      for (const d of dirs) {
        if (!d.isDirectory()) continue;
        const metaPath = path.join(meetingsDir, d.name, 'meta.json');
        if (!fs.existsSync(metaPath)) continue;
        try {
          const meta = JSON.parse(fs.readFileSync(metaPath, 'utf-8'));
          // 附加统计信息
          const transPath = path.join(meetingsDir, d.name, 'transcriptions.jsonl');
          let transcriptionCount = 0;
          if (fs.existsSync(transPath)) {
            const content = fs.readFileSync(transPath, 'utf-8');
            transcriptionCount = content.split('\n').filter((l) => l.trim()).length;
          }
          const sumPath = path.join(meetingsDir, d.name, 'summaries.json');
          let hasSummary = false;
          if (fs.existsSync(sumPath)) {
            const sumData = JSON.parse(fs.readFileSync(sumPath, 'utf-8'));
            hasSummary = (sumData.segments?.length > 0) || !!sumData.global_summary;
          }
          meetings.push({ ...meta, transcription_count: transcriptionCount, has_summary: hasSummary });
        } catch (_) { /* skip corrupt entries */ }
      }
      meetings.sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
      return { ok: true, meetings };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  // 加载指定会议的完整数据
  ipcMain.handle('meeting:load', (_event, meetingId) => {
    try {
      if (!meetingId || typeof meetingId !== 'string') {
        return { ok: false, error: 'Invalid meeting ID' };
      }
      // 防止路径遍历
      const sanitized = path.basename(meetingId);
      const dir = path.join(meetingsDir, sanitized);
      if (!fs.existsSync(dir)) return { ok: false, error: 'Meeting not found' };

      const metaPath = path.join(dir, 'meta.json');
      const meta = fs.existsSync(metaPath)
        ? JSON.parse(fs.readFileSync(metaPath, 'utf-8'))
        : {};

      const transPath = path.join(dir, 'transcriptions.jsonl');
      const transcriptions = [];
      if (fs.existsSync(transPath)) {
        const lines = fs.readFileSync(transPath, 'utf-8').split('\n');
        for (const line of lines) {
          if (line.trim()) {
            try { transcriptions.push(JSON.parse(line)); } catch (_) {}
          }
        }
      }

      const sumPath = path.join(dir, 'summaries.json');
      const summaries = fs.existsSync(sumPath)
        ? JSON.parse(fs.readFileSync(sumPath, 'utf-8'))
        : { segments: [], global_summary: null, action_items: [] };

      return { ok: true, data: { meta, transcriptions, summaries } };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  // 删除指定会议
  ipcMain.handle('meeting:delete', (_event, meetingId) => {
    try {
      if (!meetingId || typeof meetingId !== 'string') {
        return { ok: false, error: 'Invalid meeting ID' };
      }
      const sanitized = path.basename(meetingId);
      const dir = path.join(meetingsDir, sanitized);
      if (!fs.existsSync(dir)) return { ok: false, error: 'Meeting not found' };
      fs.rmSync(dir, { recursive: true, force: true });
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  // 保存会议摘要（覆盖 summaries.json）
  ipcMain.handle('meeting:save-summaries', (_event, meetingId, summaries) => {
    try {
      if (!meetingId || typeof meetingId !== 'string') {
        return { ok: false, error: 'Invalid meeting ID' };
      }
      if (!summaries || typeof summaries !== 'object') {
        return { ok: false, error: 'Invalid summaries data' };
      }
      const sanitized = path.basename(meetingId);
      const dir = path.join(meetingsDir, sanitized);
      if (!fs.existsSync(dir)) return { ok: false, error: 'Meeting not found' };

      const sumPath = path.join(dir, 'summaries.json');
      const tmpPath = sumPath + '.tmp';
      fs.writeFileSync(tmpPath, JSON.stringify(summaries, null, 2), 'utf-8');
      fs.renameSync(tmpPath, sumPath);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });
}

// ---------------------------------------------------------------
// PythonBridge 集成
// ---------------------------------------------------------------

/**
 * 初始化 PythonBridge 并将事件转发到渲染进程。
 */
function initPythonBridge() {
  let bundled, pythonPath, backendDir, projectRoot;

  if (IS_DEV) {
    // 开发模式：使用本地 venv
    bundled = false;
    pythonPath = path.resolve(BACKEND_ROOT, '.venv', 'Scripts', 'python');
    backendDir = BACKEND_ROOT;
    projectRoot = BACKEND_ROOT;
  } else {
    // 生产模式：projectRoot = resources/（.env、config/ 都在此处）
    projectRoot = process.resourcesPath;
    const exePath = path.resolve(projectRoot, 'python-backend', 'imok-backend.exe');
    if (fs.existsSync(exePath)) {
      // 完整打包模式：PyInstaller 生成的独立 exe
      bundled = true;
      pythonPath = exePath;
      backendDir = projectRoot;
    } else {
      // 轻量模式：用户自行安装的 Python + 源码
      bundled = false;
      pythonPath = 'python';
      backendDir = projectRoot;
    }
  }

  pythonBridge = new PythonBridge({
    pythonPath,
    backendDir,
    bundled,
    projectRoot,
    source: 'wasapi',
    logLevel: IS_DEV ? 'DEBUG' : 'INFO',
  });

  // Python → main → renderer 转发（主窗口 + 悬浮窗）
  const broadcast = (channel, data) => {
    mainWindow?.webContents.send(channel, data);
    const overlay = windowManager.overlayWindow;
    if (overlay) {
      overlay.webContents.send(channel, data);
    }
  };

  pythonBridge.on('transcription', (data) => {
    broadcast('python:transcription', data);
  });

  pythonBridge.on('transcription-partial', (data) => {
    broadcast('python:transcription-partial', data);
  });

  pythonBridge.on('status', (data) => {
    console.log(`[Main] python:status → state=${data.state} meeting_id=${data.meeting_id || 'N/A'}`);
    broadcast('python:status', data);
  });

  pythonBridge.on('python-error', (data) => {
    console.error('[Main] python:error →', JSON.stringify(data));
    broadcast('python:error', data);
  });

  pythonBridge.on('segment-summary', (data) => {
    broadcast('python:segment-summary', data);
  });

  pythonBridge.on('global-summary', (data) => {
    broadcast('python:global-summary', data);
  });

  pythonBridge.on('audio-level', (data) => {
    mainWindow?.webContents.send('python:audio-level', data);
  });

  pythonBridge.on('log', (text) => {
    console.log('[Python]', text);
  });

  pythonBridge.on('exit', ({ code, signal }) => {
    broadcast('python:exit', { code, signal });
  });

  pythonBridge.on('restart', (info) => {
    broadcast('python:restart', info);
  });

  pythonBridge.on('error', (data) => {
    console.error('[PythonBridge Error]', data);
    broadcast('python:bridge-error', data);
  });

  // 收到 ready 状态后通知前端，但不自动开始
  pythonBridge.on('status', (data) => {
    if (data.state === 'ready') {
      console.log('[PythonBridge] Ready, waiting for user to start meeting.');
    }
  });

  // 自动启动子进程
  pythonBridge.start();
}

// ---------------------------------------------------------------
// 全局快捷键
// ---------------------------------------------------------------

/**
 * 注册全局快捷键。
 */
function registerShortcuts() {
  // Ctrl+Shift+M: 切换闭麦面板
  globalShortcut.register('CommandOrControl+Shift+M', () => {
    mainWindow?.webContents.send('mute-panel:toggle');
  });

  // Ctrl+Shift+L: 切换字幕窗口锁定/解锁
  globalShortcut.register('CommandOrControl+Shift+L', () => {
    windowManager.toggleLockOverlay();
  });
}

// ---------------------------------------------------------------
// SSL 证书处理
// ---------------------------------------------------------------

/**
 * 根据 LLM 配置决定是否允许自签名证书。
 * 仅在 ssl_verify: false 时对 LLM API 域名放行。
 */
function setupSSLOverride() {
  const result = loadLLMConfig(BACKEND_ROOT);
  if (!result.ok || result.config.sslVerify) return;

  let allowedHost = '';
  try {
    allowedHost = new URL(result.config.baseUrl).hostname;
  } catch (_) {
    return;
  }

  session.defaultSession.setCertificateVerifyProc((request, callback) => {
    if (request.hostname === allowedHost) {
      // 信任此自签名域名
      callback(0);
    } else {
      // 其他域名使用默认验证
      callback(-3);
    }
  });

  console.log(`[main] SSL verification disabled for: ${allowedHost}`);
}

// ---------------------------------------------------------------
// 应用生命周期
// ---------------------------------------------------------------

app.whenReady().then(() => {
  setupIPC();
  createMainWindow();

  // 初始化菜单模块（需在窗口创建后）
  appMenu.init({
    getMainWindow: () => mainWindow,
    getPythonBridge: () => pythonBridge,
    IS_DEV,
    BACKEND_ROOT,
    resolvePythonExec,
  });
  appMenu.buildAppMenu();

  setupSSLOverride();
  initPythonBridge();
  registerShortcuts();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on('window-all-closed', () => {
  globalShortcut.unregisterAll();
  if (pythonBridge) {
    pythonBridge.destroy();
    pythonBridge = null;
  }
  windowManager.destroy();
  app.quit();
});
