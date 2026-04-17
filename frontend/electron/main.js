/**
 * Electron 主进程入口。
 *
 * 单一职责：管理应用生命周期和主窗口创建。
 * IPC 通道注册独立为 setupIPC()，PythonBridge 集成独立管理。
 *
 * 开发模式：加载 Vite dev server (http://localhost:5173)
 * 生产模式：加载打包后的 dist/index.html
 */

const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { PythonBridge } = require('./python-bridge');

// ---------------------------------------------------------------
// 常量
// ---------------------------------------------------------------

const IS_DEV = !app.isPackaged;
const VITE_DEV_URL = 'http://localhost:5173';
const PRELOAD_PATH = path.join(__dirname, 'preload.js');
const DIST_PATH = path.join(__dirname, '..', 'dist', 'index.html');

// ---------------------------------------------------------------
// 窗口管理
// ---------------------------------------------------------------

/** @type {BrowserWindow | null} */
let mainWindow = null;

/** @type {PythonBridge | null} */
let pythonBridge = null;

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
}

// ---------------------------------------------------------------
// PythonBridge 集成
// ---------------------------------------------------------------

/**
 * 初始化 PythonBridge 并将事件转发到渲染进程。
 */
function initPythonBridge() {
  const backendDir = IS_DEV
    ? path.resolve(__dirname, '..', '..')
    : path.resolve(process.resourcesPath, 'backend');

  pythonBridge = new PythonBridge({
    pythonPath: IS_DEV ? path.resolve(backendDir, '.venv', 'Scripts', 'python') : 'python',
    backendDir,
    source: 'wasapi',
    logLevel: IS_DEV ? 'DEBUG' : 'INFO',
  });

  // Python → main → renderer 转发
  pythonBridge.on('transcription', (data) => {
    mainWindow?.webContents.send('python:transcription', data);
  });

  pythonBridge.on('status', (data) => {
    mainWindow?.webContents.send('python:status', data);
  });

  pythonBridge.on('python-error', (data) => {
    mainWindow?.webContents.send('python:error', data);
  });

  pythonBridge.on('log', (text) => {
    if (IS_DEV) {
      console.log('[Python]', text);
    }
  });

  pythonBridge.on('exit', ({ code, signal }) => {
    mainWindow?.webContents.send('python:exit', { code, signal });
  });

  pythonBridge.on('restart', (info) => {
    mainWindow?.webContents.send('python:restart', info);
  });

  pythonBridge.on('error', (data) => {
    console.error('[PythonBridge Error]', data);
    mainWindow?.webContents.send('python:bridge-error', data);
  });
}

// ---------------------------------------------------------------
// 应用生命周期
// ---------------------------------------------------------------

app.whenReady().then(() => {
  setupIPC();
  createMainWindow();
  initPythonBridge();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (pythonBridge) {
    pythonBridge.destroy();
    pythonBridge = null;
  }
  app.quit();
});
