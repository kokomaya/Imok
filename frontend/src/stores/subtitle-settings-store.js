/**
 * 字幕悬浮窗设置存储。
 *
 * 单一职责：管理字幕外观/行为设置的响应式状态，提供 CSS 变量计算。
 * 持久化由 Electron 主进程完成（通过 IPC），本模块仅管理内存状态。
 */

import { reactive, computed } from 'vue';

// ---------------------------------------------------------------
// 默认设置
// ---------------------------------------------------------------

/** @typedef {typeof DEFAULT_SETTINGS} SubtitleSettings */

const DEFAULT_SETTINGS = {
  /** 原文字体大小 (px) */
  originalFontSize: 16,
  /** 译文字体大小 (px) */
  translationFontSize: 14,
  /** 原文颜色 */
  originalColor: '#e8e8e8',
  /** 译文颜色 */
  translationColor: '#90caf9',
  /** 背景颜色 */
  bgColor: '#141414',
  /** 显示行数（最近 N 条字幕） */
  visibleLines: 5,
  /** 是否显示翻译 */
  showTranslation: true,
  /** 是否显示时间戳 */
  showTimestamp: true,
  /** 是否锁定窗口（鼠标穿透） */
  locked: false,
  /** 是否始终置顶 */
  alwaysOnTop: true,
  /** 字幕区域不透明度 0-100 */
  subtitleOpacity: 100,
  /** 沉浸模式（隐藏控件，鼠标悬停时显示） */
  immersive: false,
  /** 字体粗细 normal | bold */
  fontWeight: 'normal',
  /** 字体 */
  fontFamily: 'default',
};

// ---------------------------------------------------------------
// 响应式状态
// ---------------------------------------------------------------

const settings = reactive({ ...DEFAULT_SETTINGS });

/**
 * 将 hex 颜色转为 rgb 数组。
 * @param {string} hex - 如 '#141414'
 * @returns {[number, number, number]}
 */
function hexToRgb(hex) {
  const h = hex.replace('#', '');
  return [
    parseInt(h.substring(0, 2), 16) || 0,
    parseInt(h.substring(2, 4), 16) || 0,
    parseInt(h.substring(4, 6), 16) || 0,
  ];
}

// ---------------------------------------------------------------
// CSS 变量计算（供 SubtitleOverlay 使用）
// ---------------------------------------------------------------

const cssVars = computed(() => {
  const [r, g, b] = hexToRgb(settings.bgColor);
  const alpha = settings.subtitleOpacity / 100;
  return {
    '--subtitle-original-size': `${settings.originalFontSize}px`,
    '--subtitle-translation-size': `${settings.translationFontSize}px`,
    '--subtitle-original-color': settings.originalColor,
    '--subtitle-translation-color': settings.translationColor,
    '--subtitle-bg': `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`,
    '--subtitle-font-weight': settings.fontWeight,
    '--subtitle-font-family': settings.fontFamily === 'default'
      ? 'inherit'
      : `"${settings.fontFamily}", sans-serif`,
  };
});

// ---------------------------------------------------------------
// 操作方法
// ---------------------------------------------------------------

/**
 * 批量更新设置。
 * @param {Partial<SubtitleSettings>} partial
 */
function update(partial) {
  for (const [key, value] of Object.entries(partial)) {
    if (key in settings) {
      settings[key] = value;
    }
  }
}

/**
 * 恢复默认设置。
 */
function resetToDefaults() {
  Object.assign(settings, DEFAULT_SETTINGS);
}

/**
 * 导出当前设置为纯对象（用于持久化）。
 * @returns {SubtitleSettings}
 */
function toJSON() {
  return { ...settings };
}

/**
 * 从纯对象加载设置（用于初始化）。
 * @param {Partial<SubtitleSettings>} data
 */
function loadFrom(data) {
  if (data && typeof data === 'object') {
    // 仅加载已知 key，忽略未知字段
    for (const key of Object.keys(DEFAULT_SETTINGS)) {
      if (key in data && typeof data[key] === typeof DEFAULT_SETTINGS[key]) {
        settings[key] = data[key];
      }
    }
  }
}

// ---------------------------------------------------------------
// 导出
// ---------------------------------------------------------------

export const subtitleSettingsStore = {
  settings,
  cssVars,
  update,
  resetToDefaults,
  toJSON,
  loadFrom,
  DEFAULT_SETTINGS,
};
