/**
 * Electron 主进程入口。
 *
 * 单一职责：管理应用生命周期和主窗口创建。
 * IPC 通道注册独立为 setupIPC()，PythonBridge 集成独立管理。
 *
 * 开发模式：加载 Vite dev server (http://localhost:5173)
 * 生产模式：加载打包后的 dist/index.html
 */

const { app, BrowserWindow, ipcMain, globalShortcut, session } = require('electron');
const path = require('path');
const fs = require('fs');
const { PythonBridge } = require('./python-bridge');
const { WindowManager } = require('./window-manager');

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
    if (pythonBridge && pythonBridge.isRunning) {
      pythonBridge.sendControl(action, extra);
      return { ok: true };
    }
    return { ok: false, error: 'Python bridge not running' };
  });

  // 启动 Python 子进程
  ipcMain.handle('python:start', () => {
    if (pythonBridge) {
      pythonBridge.start();
      return { ok: true };
    }
    return { ok: false, error: 'Python bridge not initialized' };
  });

  // 停止 Python 子进程
  ipcMain.handle('python:stop', () => {
    if (pythonBridge) {
      pythonBridge.destroy();
      pythonBridge = null;
      return { ok: true };
    }
    return { ok: false };
  });

  // 获取 Python 子进程状态
  ipcMain.handle('python:status', () => {
    return {
      running: pythonBridge ? pythonBridge.isRunning : false,
      pid: pythonBridge ? pythonBridge.pid : -1,
    };
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

  // 闭麦面板可见性切换（由快捷键触发，转发到 renderer）
  ipcMain.handle('mute-panel:toggle', () => {
    mainWindow?.webContents.send('mute-panel:toggle');
    return { ok: true };
  });

  // LLM 配置：读取 llm_providers.yaml + .env → 返回给 renderer
  ipcMain.handle('llm:get-config', () => {
    return loadLLMConfig();
  });

  // LLM 请求代理：renderer → main → VIO API（绕过 CORS）
  ipcMain.handle('llm:chat', async (_event, { messages, temperature, max_tokens }) => {
    const { net } = require('electron');
    const result = loadLLMConfig();
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
// LLM 配置读取
// ---------------------------------------------------------------

/**
 * 读取 llm_providers.yaml + .env，返回前端可用的 LLM 配置。
 * @returns {{ ok: boolean, config?: Object, error?: string }}
 */
function loadLLMConfig() {
  try {
    // 1. 读取 .env 到环境变量（简单 key=value 解析）
    const envPath = path.join(BACKEND_ROOT, '.env');
    const envVars = {};
    if (fs.existsSync(envPath)) {
      const lines = fs.readFileSync(envPath, 'utf-8').split('\n');
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;
        const eqIdx = trimmed.indexOf('=');
        if (eqIdx > 0) {
          envVars[trimmed.slice(0, eqIdx).trim()] = trimmed.slice(eqIdx + 1).trim();
        }
      }
    }

    // 2. 读取 llm_providers.yaml
    const yamlPath = path.join(BACKEND_ROOT, 'config', 'llm_providers.yaml');
    if (!fs.existsSync(yamlPath)) {
      return { ok: false, error: `Config not found: ${yamlPath}` };
    }

    // 简易 YAML 解析 — 使用 js-yaml 如果可用
    let yaml;
    try {
      yaml = require('js-yaml');
    } catch (_) {
      yaml = null;
    }

    const yamlContent = fs.readFileSync(yamlPath, 'utf-8');
    let parsed;
    if (yaml) {
      parsed = yaml.load(yamlContent);
    } else {
      parsed = parseSimpleYaml(yamlContent);
    }

    const defaultName = parsed.default_provider;
    const provider = parsed.providers?.[defaultName];
    if (!provider) {
      return { ok: false, error: `Provider '${defaultName}' not found in config` };
    }

    // 3. 解析 API token
    const tokenEnvKey = provider.api_token_env || '';
    const apiKey = tokenEnvKey
      ? (envVars[tokenEnvKey] || process.env[tokenEnvKey] || '')
      : (envVars['API_TOKEN'] || '');

    return {
      ok: true,
      config: {
        baseUrl: provider.base_url,
        model: provider.model,
        apiKey,
        headers: provider.headers || {},
        timeout: (provider.timeout || 60) * 1000,
        sslVerify: provider.ssl_verify !== false,
        stream: provider.stream !== false,
      },
    };
  } catch (err) {
    console.error('[main] Failed to load LLM config:', err.message);
    return { ok: false, error: err.message };
  }
}

/**
 * 简易 YAML 解析器 — 仅支持 llm_providers.yaml 的扁平结构。
 * @param {string} content
 * @returns {Object}
 */
function parseSimpleYaml(content) {
  const result = { providers: {} };
  let currentProvider = null;
  let inHeaders = false;

  for (const raw of content.split('\n')) {
    const line = raw.replace(/\r$/, '').replace(/#.*$/, '').trimEnd();
    if (!line.trim()) continue;

    const indent = raw.search(/\S|$/);

    if (indent === 0 && line.includes('default_provider:')) {
      result.default_provider = line.split(':').slice(1).join(':').trim().replace(/['"]/g, '');
      currentProvider = null;
      inHeaders = false;
    } else if (indent === 0 && line.trim() === 'providers:') {
      continue;
    } else if (indent === 2 && line.trim().endsWith(':') && !line.trim().includes(' ')) {
      currentProvider = line.trim().replace(/:$/, '');
      result.providers[currentProvider] = {};
      inHeaders = false;
    } else if (indent === 4 && currentProvider) {
      const kv = line.trim();
      if (kv === 'headers:') {
        inHeaders = true;
        result.providers[currentProvider].headers = {};
      } else if (inHeaders) {
        inHeaders = false;
        const ci = kv.indexOf(':');
        if (ci > 0) {
          const k = kv.slice(0, ci).trim();
          let v = kv.slice(ci + 1).trim().replace(/['"]/g, '');
          if (v === 'true') v = true;
          else if (v === 'false') v = false;
          else if (/^\d+(\.\d+)?$/.test(v)) v = Number(v);
          result.providers[currentProvider][k] = v;
        }
      } else {
        const ci = kv.indexOf(':');
        if (ci > 0) {
          const k = kv.slice(0, ci).trim();
          let v = kv.slice(ci + 1).trim().replace(/['"]/g, '');
          if (v === 'true') v = true;
          else if (v === 'false') v = false;
          else if (/^\d+(\.\d+)?$/.test(v)) v = Number(v);
          result.providers[currentProvider][k] = v;
        }
      }
    } else if (indent === 6 && currentProvider && inHeaders) {
      const kv = line.trim();
      const ci = kv.indexOf(':');
      if (ci > 0) {
        const k = kv.slice(0, ci).trim();
        const v = kv.slice(ci + 1).trim().replace(/['"]/g, '');
        result.providers[currentProvider].headers[k] = v;
      }
    }
  }

  return result;
}

// ---------------------------------------------------------------
// PythonBridge 集成
// ---------------------------------------------------------------

/**
 * 初始化 PythonBridge 并将事件转发到渲染进程。
 */
function initPythonBridge() {
  pythonBridge = new PythonBridge({
    pythonPath: IS_DEV ? path.resolve(BACKEND_ROOT, '.venv', 'Scripts', 'python') : 'python',
    backendDir: BACKEND_ROOT,
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

  pythonBridge.on('status', (data) => {
    broadcast('python:status', data);
  });

  pythonBridge.on('python-error', (data) => {
    broadcast('python:error', data);
  });

  pythonBridge.on('segment-summary', (data) => {
    broadcast('python:segment-summary', data);
  });

  pythonBridge.on('global-summary', (data) => {
    broadcast('python:global-summary', data);
  });

  pythonBridge.on('log', (text) => {
    if (IS_DEV) {
      console.log('[Python]', text);
    }
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

  // 收到 ready 状态后自动开始音频采集
  pythonBridge.on('status', (data) => {
    if (data.state === 'ready') {
      console.log('[PythonBridge] Ready, sending start command...');
      pythonBridge.sendControl('start');
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
}

// ---------------------------------------------------------------
// SSL 证书处理
// ---------------------------------------------------------------

/**
 * 根据 LLM 配置决定是否允许自签名证书。
 * 仅在 ssl_verify: false 时对 LLM API 域名放行。
 */
function setupSSLOverride() {
  const result = loadLLMConfig();
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
