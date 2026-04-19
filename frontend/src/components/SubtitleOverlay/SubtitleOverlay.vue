<script setup>
/**
 * 悬浮字幕覆盖组件。
 *
 * 单一职责：展示最近 N 条双语字幕，支持自动滚动。
 * 数据来源：subtitleStore（由 ipc-bridge 和 llm-client 驱动）。
 * 外观设置：subtitleSettingsStore（由 SubtitleSettings 面板控制）。
 */

import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue';
import { subtitleStore } from '@/stores/subtitle-store.js';
import { subtitleSettingsStore } from '@/stores/subtitle-settings-store.js';
import { ipcBridge } from '@/services/ipc-bridge.js';
import { llmClient } from '@/services/llm-client.js';
import SubtitleSettings from './SubtitleSettings.vue';

const listRef = ref(null);
const showSettings = ref(false);

const { settings, cssVars } = subtitleSettingsStore;

// 根据设置中的 visibleLines 截取字幕
const displayEntries = computed(() => {
  return subtitleStore.state.entries.slice(-settings.visibleLines);
});

// 自动滚动到底部
watch(displayEntries, async () => {
  await nextTick();
  scrollToBottom();
}, { deep: true });

function scrollToBottom() {
  if (listRef.value) {
    listRef.value.scrollTop = listRef.value.scrollHeight;
  }
}

function formatTime(timestamp) {
  return new Date(timestamp).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function getStatusClass(status) {
  switch (status) {
    case 'translating': return 'translating';
    case 'done': return 'done';
    case 'error': return 'error';
    default: return 'pending';
  }
}

// ---------------------------------------------------------------
// 设置持久化（防抖）
// ---------------------------------------------------------------

let saveTimer = null;

function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    if (window.electronAPI?.saveOverlaySettings) {
      window.electronAPI.saveOverlaySettings(subtitleSettingsStore.toJSON());
    }
  }, 300);
}

function onLockToggle(locked) {
  if (window.electronAPI?.setOverlayClickThrough) {
    window.electronAPI.setOverlayClickThrough(locked);
  }
}

function onSettingsSave() {
  // 同步 always-on-top 到 Electron
  if (window.electronAPI?.setOverlayAlwaysOnTop) {
    window.electronAPI.setOverlayAlwaysOnTop(settings.alwaysOnTop);
  }
  scheduleSave();
}

// ---------------------------------------------------------------
// 生命周期
// ---------------------------------------------------------------

onMounted(async () => {
  // 加载持久化设置
  if (window.electronAPI?.getOverlaySettings) {
    try {
      const result = await window.electronAPI.getOverlaySettings();
      if (result.ok && result.settings) {
        subtitleSettingsStore.loadFrom(result.settings);
      }
    } catch (_) {
      // 使用默认设置
    }
  }

  // 同步初始 always-on-top / click-through 状态
  if (window.electronAPI?.setOverlayAlwaysOnTop) {
    window.electronAPI.setOverlayAlwaysOnTop(settings.alwaysOnTop);
  }
  if (window.electronAPI?.setOverlayClickThrough) {
    window.electronAPI.setOverlayClickThrough(settings.locked);
  }

  // 初始化 IPC 桥接，当有新转写时触发翻译
  ipcBridge.init({
    onTranscription: (entry) => {
      if (llmClient.isConfigured()) {
        llmClient.translateEntry(entry);
      }
    },
  });
});

onUnmounted(() => {
  if (saveTimer) clearTimeout(saveTimer);
  ipcBridge.destroy();
});
</script>

<template>
  <div class="overlay-root" :style="cssVars">
    <!-- 拖拽条 -->
    <div class="drag-handle" style="-webkit-app-region: drag">
      <span class="drag-label">字幕</span>
      <div class="drag-actions" style="-webkit-app-region: no-drag">
        <button
          class="handle-btn"
          @click="showSettings = !showSettings"
          title="设置"
        >⚙</button>
      </div>
      <span
        class="status-dot"
        :class="subtitleStore.state.pythonStatus"
      ></span>
    </div>

    <!-- 设置面板 -->
    <SubtitleSettings
      v-if="showSettings"
      @save="onSettingsSave"
      @lock-toggle="onLockToggle"
      @close="showSettings = false"
    />

    <!-- 字幕列表 -->
    <div ref="listRef" class="subtitle-list">
      <TransitionGroup name="subtitle">
        <div
          v-for="entry in displayEntries"
          :key="entry.id"
          class="subtitle-entry"
        >
          <div class="entry-header" v-if="settings.showTimestamp">
            <span class="time">{{ formatTime(entry.timestamp) }}</span>
            <span class="lang-badge" v-if="entry.language">
              {{ entry.language }}
            </span>
          </div>
          <div class="entry-original">{{ entry.original }}</div>
          <div
            class="entry-translation"
            :class="getStatusClass(entry.translationStatus)"
            v-if="settings.showTranslation && (entry.translation || entry.translationStatus === 'translating')"
          >
            {{ entry.translation }}
            <span
              v-if="entry.translationStatus === 'translating'"
              class="typing-indicator"
            >▍</span>
          </div>
          <div
            v-if="settings.showTranslation && entry.translationStatus === 'error'"
            class="entry-translation error"
          >
            翻译失败
          </div>
        </div>
      </TransitionGroup>

      <div
        v-if="displayEntries.length === 0"
        class="empty-hint"
      >
        等待语音输入…
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay-root {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: rgba(20, 20, 20, var(--subtitle-bg-opacity, 0.85));
  border-radius: 8px;
  overflow: hidden;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif;
  color: var(--subtitle-original-color, #e8e8e8);
  user-select: none;
}

/* 拖拽条 */
.drag-handle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 12px;
  background: rgba(255, 255, 255, 0.06);
  cursor: move;
  flex-shrink: 0;
}

.drag-label {
  font-size: 11px;
  color: #888;
  letter-spacing: 1px;
  text-transform: uppercase;
}

.drag-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.handle-btn {
  font-size: 13px;
  padding: 1px 4px;
  border: none;
  background: transparent;
  color: #888;
  cursor: pointer;
  border-radius: 3px;
  transition: all 0.15s;
  line-height: 1;
}

.handle-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #ddd;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #666;
  flex-shrink: 0;
}

.status-dot.running {
  background: #4caf50;
  box-shadow: 0 0 4px #4caf50;
}

.status-dot.ready {
  background: #ff9800;
}

.status-dot.stopped,
.status-dot.disconnected {
  background: #666;
}

.status-dot.error,
.status-dot.crashed {
  background: #f44336;
}

.status-dot.restarting {
  background: #ff9800;
  animation: pulse 1s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

/* 字幕列表 */
.subtitle-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.subtitle-list::-webkit-scrollbar {
  width: 4px;
}

.subtitle-list::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.15);
  border-radius: 2px;
}

/* 字幕条目 */
.subtitle-entry {
  padding: 6px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.subtitle-entry:last-child {
  border-bottom: none;
}

.entry-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}

.time {
  font-size: 10px;
  color: #666;
  font-variant-numeric: tabular-nums;
}

.lang-badge {
  font-size: 9px;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.1);
  color: #aaa;
  text-transform: uppercase;
}

.entry-original {
  font-size: var(--subtitle-original-size, 14px);
  font-weight: var(--subtitle-font-weight, normal);
  line-height: 1.4;
  color: var(--subtitle-original-color, #e8e8e8);
}

.entry-translation {
  font-size: var(--subtitle-translation-size, 13px);
  font-weight: var(--subtitle-font-weight, normal);
  line-height: 1.4;
  color: var(--subtitle-translation-color, #90caf9);
  margin-top: 2px;
}

.entry-translation.translating {
  color: var(--subtitle-translation-color, #90caf9);
}

.entry-translation.done {
  color: #81c784;
}

.entry-translation.error {
  color: #ef5350;
  font-style: italic;
}

.typing-indicator {
  animation: blink 0.8s infinite;
  color: var(--subtitle-translation-color, #90caf9);
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* 空状态 */
.empty-hint {
  color: #555;
  font-style: italic;
  font-size: 13px;
  text-align: center;
  margin-top: 20px;
}

/* TransitionGroup 动画 */
.subtitle-enter-active {
  transition: all 0.3s ease;
}

.subtitle-leave-active {
  transition: all 0.2s ease;
}

.subtitle-enter-from {
  opacity: 0;
  transform: translateY(10px);
}

.subtitle-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}
</style>
