/**
 * 应用菜单 + 音频设备管理。
 *
 * 单一职责：构建原生应用菜单，管理音频设备选择/测试。
 * 通过 init() 接收主进程上下文引用，避免循环依赖。
 */

const { Menu } = require('electron');
const path = require('path');

// ---------------------------------------------------------------
// 模块内部状态
// ---------------------------------------------------------------

/** 当前选中的设备索引（null = 自动/默认） */
let selectedLoopbackDevice = null;
let selectedMicDevice = null;

/** 缓存的设备列表 */
let cachedDevices = null;

// ---------------------------------------------------------------
// 上下文引用（通过 init 注入）
// ---------------------------------------------------------------

/** @type {() => import('electron').BrowserWindow | null} */
let _getMainWindow = () => null;

/** @type {() => import('./python-bridge').PythonBridge | null} */
let _getPythonBridge = () => null;

let _IS_DEV = false;
let _BACKEND_ROOT = '';
let _resolvePythonExec = null;

/**
 * 初始化模块 — 注入主进程上下文。
 * @param {{ getMainWindow: Function, getPythonBridge: Function, IS_DEV: boolean, BACKEND_ROOT: string, resolvePythonExec: Function }} ctx
 */
function init(ctx) {
  _getMainWindow = ctx.getMainWindow;
  _getPythonBridge = ctx.getPythonBridge;
  _IS_DEV = ctx.IS_DEV;
  _BACKEND_ROOT = ctx.BACKEND_ROOT;
  _resolvePythonExec = ctx.resolvePythonExec;
}

// ---------------------------------------------------------------
// 菜单构建
// ---------------------------------------------------------------

/**
 * 构建应用菜单 — 菜单项与工具栏功能一一对应。
 * 菜单通过 IPC 发送 'menu:action' 事件到渲染进程，由 App.vue 统一处理。
 */
function buildAppMenu() {
  const send = (action, data) => {
    _getMainWindow()?.webContents.send('menu:action', action, data);
  };

  const template = [
    {
      label: '会议',
      submenu: [
        {
          label: '开始会议',
          accelerator: 'CmdOrCtrl+Shift+S',
          click: () => send('start-meeting'),
        },
        {
          label: '停止会议',
          accelerator: 'CmdOrCtrl+Shift+X',
          click: () => send('stop-meeting'),
        },
        { type: 'separator' },
        {
          label: '历史会议记录',
          accelerator: 'CmdOrCtrl+H',
          click: () => send('toggle-history'),
        },
        { type: 'separator' },
        { role: 'quit', label: '退出' },
      ],
    },
    {
      label: '音频',
      submenu: [
        {
          label: '系统音频 (Teams/Zoom)',
          type: 'checkbox',
          checked: true,
          id: 'system-audio',
          click: (item) => send(item.checked ? 'enable-system-audio' : 'disable-system-audio'),
        },
        {
          label: '麦克风',
          type: 'checkbox',
          checked: true,
          id: 'mic-audio',
          click: (item) => send(item.checked ? 'enable-mic' : 'disable-mic'),
        },
        { type: 'separator' },
        {
          label: '🔊 选择系统音频设备…',
          click: () => openDeviceSelector('loopback'),
        },
        {
          label: '🎤 选择麦克风设备…',
          click: () => openDeviceSelector('mic'),
        },
        { type: 'separator' },
        {
          label: '🔍 刷新设备列表',
          click: () => { cachedDevices = null; refreshDeviceMenu(); },
        },
        { type: 'separator' },
        {
          label: '🎛 音频设备监控面板',
          accelerator: 'CmdOrCtrl+Shift+D',
          click: () => send('toggle-device-panel'),
        },
      ],
    },
    {
      label: '视图',
      submenu: [
        {
          label: '悬浮字幕',
          accelerator: 'CmdOrCtrl+Shift+O',
          click: () => send('open-overlay'),
        },
        {
          label: '闭麦表达助手',
          accelerator: 'CmdOrCtrl+Shift+M',
          click: () => send('toggle-mute-assist'),
        },
        {
          label: '会议摘要面板',
          accelerator: 'CmdOrCtrl+Shift+P',
          click: () => send('toggle-summary'),
        },
        { type: 'separator' },
        {
          label: '清空转写记录',
          click: () => send('clear-transcriptions'),
        },
        { type: 'separator' },
        { role: 'toggleDevTools', label: '开发者工具' },
        { role: 'reload', label: '重新加载' },
      ],
    },
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

// ---------------------------------------------------------------
// 设备管理
// ---------------------------------------------------------------

/**
 * 异步获取设备列表（带缓存）。
 * @returns {Promise<{ loopback: Array, input: Array } | null>}
 */
async function fetchDevices() {
  if (cachedDevices) return cachedDevices;

  const { execFile } = require('child_process');
  const { pythonPath, args, execOpts } = _resolvePythonExec(['backend.audio.list_devices']);

  return new Promise((resolve) => {
    execFile(
      pythonPath,
      args,
      { ...execOpts },
      (err, stdout) => {
        if (err) { resolve(null); return; }
        try {
          cachedDevices = JSON.parse(stdout);
          resolve(cachedDevices);
        } catch (_) {
          resolve(null);
        }
      },
    );
  });
}

/**
 * 打开设备选择子菜单（弹出式）。
 * @param {'loopback' | 'mic'} deviceType
 */
async function openDeviceSelector(deviceType) {
  const devices = await fetchDevices();
  if (!devices) {
    const { dialog } = require('electron');
    dialog.showErrorBox('设备枚举失败', '无法获取音频设备列表，请检查 Python 环境。');
    return;
  }

  const list = deviceType === 'loopback' ? (devices.loopback || []) : (devices.input || []);
  const currentSelection = deviceType === 'loopback' ? selectedLoopbackDevice : selectedMicDevice;
  const mainWindow = _getMainWindow();

  if (list.length === 0) {
    const { dialog } = require('electron');
    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: '无可用设备',
      message: deviceType === 'loopback'
        ? '未检测到 WASAPI Loopback 设备。请确认 Windows 有活跃的音频输出设备。'
        : '未检测到麦克风设备。',
    });
    return;
  }

  const menuItems = [
    {
      label: '自动检测（默认）',
      type: 'radio',
      checked: currentSelection === null,
      click: () => selectDevice(deviceType, null),
    },
    { type: 'separator' },
  ];

  for (const dev of list) {
    const isSelected = currentSelection === dev.index;
    const suffix = dev.isDefault ? ' ★' : '';
    menuItems.push({
      label: `${dev.name}${suffix}`,
      sublabel: `${dev.hostApi} | ${dev.sampleRate}Hz | idx:${dev.index}`,
      type: 'radio',
      checked: isSelected,
      click: () => selectDevice(deviceType, dev.index),
    });
  }

  menuItems.push(
    { type: 'separator' },
    {
      label: '🔊 测试选中设备…',
      click: () => testDevice(deviceType),
    },
  );

  const popupMenu = Menu.buildFromTemplate(menuItems);
  popupMenu.popup({ window: mainWindow });
}

/**
 * 选择设备。
 * @param {'loopback' | 'mic'} deviceType
 * @param {number | null} deviceIndex
 */
function selectDevice(deviceType, deviceIndex) {
  if (deviceType === 'loopback') {
    selectedLoopbackDevice = deviceIndex;
  } else {
    selectedMicDevice = deviceIndex;
  }

  // 通知 Python 子进程更新设备配置（下次启动 pipeline 生效）
  const bridge = _getPythonBridge();
  if (bridge && bridge.isRunning) {
    bridge.sendControl('set_devices', {
      loopback_device: selectedLoopbackDevice,
      mic_device: selectedMicDevice,
    });
  }

  // 通知渲染进程更新 UI
  _getMainWindow()?.webContents.send('menu:action', 'device-changed', {
    loopbackDevice: selectedLoopbackDevice,
    micDevice: selectedMicDevice,
  });

  console.log(`[audio] Device selected: ${deviceType} = ${deviceIndex}`);
}

/**
 * 测试当前选中的设备（录音 3 秒，显示结果对话框）。
 * @param {'loopback' | 'mic'} deviceType
 */
async function testDevice(deviceType) {
  const { dialog } = require('electron');
  const deviceIndex = deviceType === 'loopback' ? selectedLoopbackDevice : selectedMicDevice;
  const mainWindow = _getMainWindow();

  if (deviceIndex === null) {
    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: '测试设备',
      message: '请先选择一个具体设备再测试。',
    });
    return;
  }

  // 通知用户正在测试
  mainWindow?.webContents.send('menu:action', 'device-testing', { type: deviceType, index: deviceIndex });

  const { execFile } = require('child_process');
  const { pythonPath, args, execOpts } = _resolvePythonExec([
    'backend.audio.test_device', `--type=${deviceType}`, `--index=${deviceIndex}`, '--seconds=3',
  ]);

  const result = await new Promise((resolve) => {
    execFile(
      pythonPath,
      args,
      execOpts,
      (err, stdout) => {
        if (err) return resolve({ ok: false, error: err.message });
        try { return resolve(JSON.parse(stdout)); }
        catch (_) { return resolve({ ok: false, error: 'Parse error' }); }
      },
    );
  });

  mainWindow?.webContents.send('menu:action', 'device-test-result', result);

  if (!result.ok) {
    dialog.showErrorBox('设备测试失败', result.error || '未知错误');
    return;
  }

  const statusIcon = result.hasSignal ? '✅' : '⚠️';
  const peakDb = result.peak > 0 ? (20 * Math.log10(result.peak)).toFixed(1) : '-∞';
  const rmsDb = result.rms > 0 ? (20 * Math.log10(result.rms)).toFixed(1) : '-∞';

  dialog.showMessageBox(mainWindow, {
    type: result.hasSignal ? 'info' : 'warning',
    title: '设备测试结果',
    message: `${statusIcon} ${deviceType === 'loopback' ? '系统音频' : '麦克风'} 设备测试完成`,
    detail: [
      `峰值: ${peakDb} dB (${(result.peak * 100).toFixed(1)}%)`,
      `均值: ${rmsDb} dB`,
      `时长: ${result.duration}s`,
      result.clipped ? '⚠ 检测到削波（音量过大）' : '',
      !result.hasSignal ? '⚠ 未检测到有效信号，请确认设备正在播放/输入音频' : '',
    ].filter(Boolean).join('\n'),
  });
}

/**
 * 刷新设备缓存。
 */
async function refreshDeviceMenu() {
  cachedDevices = null;
  await fetchDevices();
  console.log('[audio] Device cache refreshed');
}

/**
 * 从渲染进程同步音频开关状态到菜单勾选。
 * @param {{ systemAudio: boolean, mic: boolean }} state
 */
function updateAudioMenuChecks(state) {
  const menu = Menu.getApplicationMenu();
  if (!menu) return;
  const sysItem = menu.getMenuItemById('system-audio');
  const micItem = menu.getMenuItemById('mic-audio');
  if (sysItem) sysItem.checked = state.systemAudio;
  if (micItem) micItem.checked = state.mic;
}

module.exports = {
  init,
  buildAppMenu,
  updateAudioMenuChecks,
};
