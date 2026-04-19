<script setup>
/**
 * 字幕悬浮窗设置面板。
 *
 * 单一职责：提供 QQ 音乐歌词风格的设置控件 UI。
 * 数据源：subtitleSettingsStore。
 * 不负责持久化（由父组件通过 emit 触发保存）。
 */

import { subtitleSettingsStore } from '@/stores/subtitle-settings-store.js';

const emit = defineEmits(['save', 'lock-toggle', 'close']);

const { settings, DEFAULT_SETTINGS } = subtitleSettingsStore;

const FONT_OPTIONS = [
  { value: 'default', label: '默认' },
  { value: 'Microsoft YaHei', label: '微软雅黑' },
  { value: 'SimHei', label: '黑体' },
  { value: 'KaiTi', label: '楷体' },
  { value: 'Source Han Sans CN', label: '思源黑体' },
  { value: 'Consolas', label: 'Consolas' },
  { value: 'Arial', label: 'Arial' },
];

function onLockToggle() {
  settings.locked = !settings.locked;
  emit('lock-toggle', settings.locked);
  emit('save');
}

function onAlwaysOnTopToggle() {
  settings.alwaysOnTop = !settings.alwaysOnTop;
  emit('save');
}

function onChange() {
  emit('save');
}

function onReset() {
  subtitleSettingsStore.resetToDefaults();
  emit('save');
}
</script>

<template>
  <div class="settings-panel">
    <!-- 行 1：字体控制 -->
    <div class="settings-row">
      <label class="setting-label">原文</label>
      <input
        type="range"
        class="setting-slider"
        v-model.number="settings.originalFontSize"
        :min="12" :max="32" :step="1"
        @input="onChange"
        title="原文字号"
      />
      <span class="setting-value">{{ settings.originalFontSize }}</span>
      <input
        type="color"
        class="setting-color"
        v-model="settings.originalColor"
        @input="onChange"
        title="原文颜色"
      />
    </div>
    <div class="settings-preview" :style="{ fontSize: settings.originalFontSize + 'px', color: settings.originalColor, fontWeight: settings.fontWeight, fontFamily: settings.fontFamily === 'default' ? 'inherit' : settings.fontFamily }">
      字幕预览 Subtitle Preview
    </div>

    <div class="settings-row">
      <label class="setting-label">译文</label>
      <input
        type="range"
        class="setting-slider"
        v-model.number="settings.translationFontSize"
        :min="10" :max="28" :step="1"
        @input="onChange"
        title="译文字号"
      />
      <span class="setting-value">{{ settings.translationFontSize }}</span>
      <input
        type="color"
        class="setting-color"
        v-model="settings.translationColor"
        @input="onChange"
        title="译文颜色"
      />
    </div>
    <div class="settings-preview" :style="{ fontSize: settings.translationFontSize + 'px', color: settings.translationColor, fontWeight: settings.fontWeight, fontFamily: settings.fontFamily === 'default' ? 'inherit' : settings.fontFamily }">
      翻译预览 Translation Preview
    </div>

    <!-- 行 2：背景、行数、透明度 -->
    <div class="settings-row">
      <label class="setting-label">背景</label>
      <input
        type="color"
        class="setting-color"
        v-model="settings.bgColor"
        @input="onChange"
        title="背景颜色"
      />
    </div>

    <div class="settings-row">
      <label class="setting-label">行数</label>
      <input
        type="range"
        class="setting-slider"
        v-model.number="settings.visibleLines"
        :min="1" :max="10" :step="1"
        @input="onChange"
        title="显示字幕行数"
      />
      <span class="setting-value">{{ settings.visibleLines }}</span>
    </div>

    <div class="settings-row">
      <label class="setting-label">透明</label>
      <input
        type="range"
        class="setting-slider"
        v-model.number="settings.subtitleOpacity"
        :min="10" :max="100" :step="1"
        @input="onChange"
        title="字幕区域透明度"
      />
      <span class="setting-value">{{ settings.subtitleOpacity }}%</span>
    </div>

    <div class="settings-row">
      <label class="setting-label">字体</label>
      <select
        class="setting-select"
        v-model="settings.fontFamily"
        @change="onChange"
        title="字体"
      >
        <option
          v-for="opt in FONT_OPTIONS"
          :key="opt.value"
          :value="opt.value"
          :style="{ fontFamily: opt.value === 'default' ? 'inherit' : opt.value }"
        >{{ opt.label }}</option>
      </select>
    </div>

    <!-- 行 3：开关控制 -->
    <div class="settings-row settings-toggles">
      <button
        class="toggle-btn"
        :class="{ active: settings.showTranslation }"
        @click="settings.showTranslation = !settings.showTranslation; onChange()"
        title="显示/隐藏翻译"
      >译</button>
      <button
        class="toggle-btn"
        :class="{ active: settings.showTimestamp }"
        @click="settings.showTimestamp = !settings.showTimestamp; onChange()"
        title="显示/隐藏时间戳"
      >⏱</button>
      <button
        class="toggle-btn"
        :class="{ active: settings.fontWeight === 'bold' }"
        @click="settings.fontWeight = settings.fontWeight === 'bold' ? 'normal' : 'bold'; onChange()"
        title="字体加粗"
      >B</button>
      <button
        class="toggle-btn"
        :class="{ active: settings.immersive }"
        @click="settings.immersive = !settings.immersive; onChange()"
        title="沉浸模式（隐藏控件，悬停时显示）"
      >👁</button>
      <button
        class="toggle-btn"
        :class="{ active: settings.locked }"
        @click="onLockToggle"
        title="锁定/解锁窗口（锁定后鼠标穿透，Ctrl+Shift+L 解锁）"
      >{{ settings.locked ? '🔒' : '🔓' }}</button>
      <button
        class="toggle-btn"
        :class="{ active: settings.alwaysOnTop }"
        @click="onAlwaysOnTopToggle"
        title="始终置顶"
      >📌</button>
      <button
        class="toggle-btn reset-btn"
        @click="onReset"
        title="恢复默认设置"
      >↺</button>
      <button
        class="toggle-btn close-settings-btn"
        @click="emit('close')"
        title="关闭设置"
      >✕</button>
    </div>
  </div>
</template>

<style scoped src="./SubtitleSettings.scoped.css"></style>
