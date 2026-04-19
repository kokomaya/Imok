/**
 * 悬浮字幕窗口管理器。
 *
 * 单一职责：创建和管理半透明、无边框、始终置顶的字幕悬浮窗。
 * 包括窗口位置/尺寸持久化、拖拽与缩放支持。
 *
 * 不负责渲染内容或 IPC 通信。
 */

const { BrowserWindow, screen } = require('electron');
const path = require('path');
const fs = require('fs');

// ---------------------------------------------------------------
// 常量
// ---------------------------------------------------------------

const IS_DEV = process.env.NODE_ENV === 'development' || !require('electron').app.isPackaged;
const VITE_DEV_URL = 'http://localhost:5173';
const PRELOAD_PATH = path.join(__dirname, 'preload.js');
const DIST_PATH = path.join(__dirname, '..', 'dist', 'index.html');

/** 窗口位置配置文件路径 */
const BOUNDS_FILE = path.join(
  require('electron').app.getPath('userData'),
  'subtitle-window-bounds.json',
);

/** 字幕设置配置文件路径 */
const SETTINGS_FILE = path.join(
  require('electron').app.getPath('userData'),
  'subtitle-settings.json',
);

/** 默认窗口尺寸 */
const DEFAULT_BOUNDS = {
  width: 600,
  height: 200,
};

// ---------------------------------------------------------------
// 持久化辅助
// ---------------------------------------------------------------

/**
 * 读取保存的窗口位置。
 * @returns {{ x?: number, y?: number, width: number, height: number }}
 */
function loadBounds() {
  try {
    if (fs.existsSync(BOUNDS_FILE)) {
      const data = JSON.parse(fs.readFileSync(BOUNDS_FILE, 'utf-8'));
      if (data && typeof data.width === 'number' && typeof data.height === 'number') {
        return data;
      }
    }
  } catch (_) {
    // 文件损坏或不可读，使用默认值
  }
  return { ...DEFAULT_BOUNDS };
}

/**
 * 保存窗口位置。
 * @param {{ x: number, y: number, width: number, height: number }} bounds
 */
function saveBounds(bounds) {
  try {
    fs.writeFileSync(BOUNDS_FILE, JSON.stringify(bounds), 'utf-8');
  } catch (_) {
    // 写入失败（权限等），静默忽略
  }
}

/**
 * 确保窗口位置在可见屏幕范围内。
 * @param {{ x?: number, y?: number, width: number, height: number }} bounds
 * @returns {{ x: number, y: number, width: number, height: number }}
 */
function ensureVisibleBounds(bounds) {
  const { width, height } = bounds;
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width: screenW, height: screenH } = primaryDisplay.workAreaSize;

  let x = bounds.x;
  let y = bounds.y;

  // 如果没有保存的位置，居中底部
  if (x === undefined || y === undefined) {
    x = Math.round((screenW - width) / 2);
    y = screenH - height - 40;
  }

  // 确保至少有一部分在屏幕内
  x = Math.max(-width + 50, Math.min(x, screenW - 50));
  y = Math.max(0, Math.min(y, screenH - 50));

  return { x, y, width, height };
}

// ---------------------------------------------------------------
// WindowManager
// ---------------------------------------------------------------

class WindowManager {
  constructor() {
    /** @type {BrowserWindow | null} */
    this._overlayWindow = null;

    /** 防抖定时器 */
    this._boundsTimer = null;
  }

  /**
   * 创建字幕悬浮窗。若已存在则聚焦。
   * @returns {BrowserWindow}
   */
  createOverlayWindow() {
    if (this._overlayWindow && !this._overlayWindow.isDestroyed()) {
      this._overlayWindow.focus();
      return this._overlayWindow;
    }

    const savedBounds = loadBounds();
    const bounds = ensureVisibleBounds(savedBounds);

    this._overlayWindow = new BrowserWindow({
      ...bounds,
      minWidth: 300,
      minHeight: 100,
      frame: false,
      transparent: true,
      alwaysOnTop: true,
      resizable: true,
      skipTaskbar: true,
      hasShadow: false,
      webPreferences: {
        preload: PRELOAD_PATH,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });

    // 允许鼠标穿透时忽略非内容区域（由渲染进程控制）
    this._overlayWindow.setIgnoreMouseEvents(false);

    // 加载页面（与主窗口共用同一 Vite 应用，通过 hash 路由区分）
    if (IS_DEV) {
      this._overlayWindow.loadURL(`${VITE_DEV_URL}#/overlay`);
    } else {
      this._overlayWindow.loadFile(DIST_PATH, { hash: '/overlay' });
    }

    // 窗口移动/缩放时持久化位置（防抖 500ms）
    const persistBounds = () => {
      if (this._boundsTimer) clearTimeout(this._boundsTimer);
      this._boundsTimer = setTimeout(() => {
        if (this._overlayWindow && !this._overlayWindow.isDestroyed()) {
          saveBounds(this._overlayWindow.getBounds());
        }
      }, 500);
    };

    this._overlayWindow.on('moved', persistBounds);
    this._overlayWindow.on('resized', persistBounds);

    this._overlayWindow.on('closed', () => {
      this._overlayWindow = null;
    });

    return this._overlayWindow;
  }

  /**
   * 获取字幕悬浮窗实例。
   * @returns {BrowserWindow | null}
   */
  get overlayWindow() {
    if (this._overlayWindow && this._overlayWindow.isDestroyed()) {
      this._overlayWindow = null;
    }
    return this._overlayWindow;
  }

  /**
   * 切换字幕窗口置顶状态。
   * @param {boolean} onTop
   */
  setAlwaysOnTop(onTop) {
    if (this._overlayWindow && !this._overlayWindow.isDestroyed()) {
      this._overlayWindow.setAlwaysOnTop(onTop);
    }
  }

  /**
   * 切换鼠标穿透（用于"锁定"模式——内容只读，鼠标点击穿透到下层窗口）。
   * @param {boolean} clickThrough
   */
  setClickThrough(clickThrough) {
    if (this._overlayWindow && !this._overlayWindow.isDestroyed()) {
      this._overlayWindow.setIgnoreMouseEvents(clickThrough, { forward: true });
    }
  }
  /**
   * 切换锁定状态并通知渲染进程同步 UI。
   * 由全局快捷键触发，解决锁定后无法点击解锁的问题。
   */
  toggleLockOverlay() {
    if (!this._overlayWindow || this._overlayWindow.isDestroyed()) return;
    // 读取当前持久化设置中的 locked 状态
    const settings = this.loadSettings();
    const wasLocked = !!settings.locked;
    const newLocked = !wasLocked;
    // 切换窗口穿透
    this.setClickThrough(newLocked);
    // 持久化
    settings.locked = newLocked;
    this.saveSettings(settings);
    // 通知渲染进程同步 UI
    this._overlayWindow.webContents.send('overlay:toggle-lock', newLocked);
  }
  /**
   * 关闭字幕窗口。
   */
  closeOverlay() {
    if (this._overlayWindow && !this._overlayWindow.isDestroyed()) {
      this._overlayWindow.close();
      this._overlayWindow = null;
    }
  }

  // ---------------------------------------------------------------
  // 字幕设置持久化
  // ---------------------------------------------------------------

  /**
   * 读取持久化的字幕设置。
   * @returns {Object} 设置对象（可能为空 {}）
   */
  loadSettings() {
    try {
      if (fs.existsSync(SETTINGS_FILE)) {
        return JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf-8'));
      }
    } catch (_) {
      // 文件损坏或不可读，返回空对象使用默认值
    }
    return {};
  }

  /**
   * 持久化字幕设置。
   * @param {Object} settings
   */
  saveSettings(settings) {
    try {
      fs.writeFileSync(SETTINGS_FILE, JSON.stringify(settings, null, 2), 'utf-8');
    } catch (_) {
      // 写入失败，静默忽略
    }
  }

  /**
   * 销毁所有资源。
   */
  destroy() {
    if (this._boundsTimer) {
      clearTimeout(this._boundsTimer);
      this._boundsTimer = null;
    }
    this.closeOverlay();
  }
}

module.exports = { WindowManager };
