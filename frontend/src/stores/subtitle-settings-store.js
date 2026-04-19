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
  /** 背景不透明度 0-100 */
  bgOpacity: 85,
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
  /** 字体粗细 normal | bold */
  fontWeight: 'normal',
};

// ---------------------------------------------------------------
// 响应式状态
// ---------------------------------------------------------------

const settings = reactive({ ...DEFAULT_SETTINGS });

// ---------------------------------------------------------------
// CSS 变量计算（供 SubtitleOverlay 使用）
// ---------------------------------------------------------------

const cssVars = computed(() => ({
  '--subtitle-original-size': `${settings.originalFontSize}px`,
  '--subtitle-translation-size': `${settings.translationFontSize}px`,
  '--subtitle-original-color': settings.originalColor,
  '--subtitle-translation-color': settings.translationColor,
  '--subtitle-bg-opacity': (settings.bgOpacity / 100).toFixed(2),
  '--subtitle-font-weight': settings.fontWeight,
}));

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
