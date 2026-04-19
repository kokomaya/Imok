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

    <!-- 行 2：背景与行数 -->
    <div class="settings-row">
      <label class="setting-label">背景</label>
      <input
        type="range"
        class="setting-slider"
        v-model.number="settings.bgOpacity"
        :min="0" :max="100" :step="5"
        @input="onChange"
        title="背景不透明度"
      />
      <span class="setting-value">{{ settings.bgOpacity }}%</span>
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
        :class="{ active: settings.locked }"
        @click="onLockToggle"
        title="锁定/解锁窗口（锁定后鼠标穿透）"
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

<style scoped>
.settings-panel {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 10px;
  background: rgba(30, 30, 30, 0.95);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  -webkit-app-region: no-drag;
}

.settings-row {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 22px;
}

.setting-label {
  font-size: 11px;
  color: #888;
  width: 28px;
  flex-shrink: 0;
  text-align: right;
}

.setting-slider {
  flex: 1;
  height: 4px;
  -webkit-appearance: none;
  appearance: none;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 2px;
  outline: none;
  cursor: pointer;
}

.setting-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #64b5f6;
  cursor: pointer;
}

.setting-value {
  font-size: 10px;
  color: #aaa;
  width: 28px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.setting-color {
  width: 20px;
  height: 20px;
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 3px;
  padding: 0;
  cursor: pointer;
  background: none;
  flex-shrink: 0;
}

.setting-color::-webkit-color-swatch-wrapper {
  padding: 1px;
}

.setting-color::-webkit-color-swatch {
  border: none;
  border-radius: 2px;
}

.settings-toggles {
  justify-content: center;
  gap: 4px;
  padding-top: 2px;
}

.toggle-btn {
  font-size: 12px;
  padding: 2px 8px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.05);
  color: #888;
  cursor: pointer;
  transition: all 0.15s;
  line-height: 1.4;
}

.toggle-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #ccc;
}

.toggle-btn.active {
  background: rgba(100, 181, 246, 0.2);
  border-color: rgba(100, 181, 246, 0.4);
  color: #64b5f6;
}

.reset-btn {
  margin-left: auto;
}

.close-settings-btn {
  color: #999;
}
.close-settings-btn:hover {
  color: #f44336;
}
</style>
