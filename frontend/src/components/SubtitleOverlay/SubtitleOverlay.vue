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
let _unlockCleanup = null;

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

  // 监听全局快捷键切换锁定状态（解决锁定后无法点击解锁的问题）
  if (window.electronAPI?.on) {
    _unlockCleanup = window.electronAPI.on('overlay:toggle-lock', (locked) => {
      settings.locked = locked;
      scheduleSave();
    });
  }
});

onUnmounted(() => {
  if (saveTimer) clearTimeout(saveTimer);
  if (_unlockCleanup) _unlockCleanup();
  ipcBridge.destroy();
});
</script>

<template>
  <div class="overlay-root" :class="{ immersive: settings.immersive }" :style="cssVars">
    <!-- 拖拽条 -->
    <div class="drag-handle overlay-chrome" style="-webkit-app-region: drag">
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
    <div class="overlay-chrome" v-if="showSettings">
      <SubtitleSettings
        @save="onSettingsSave"
        @lock-toggle="onLockToggle"
        @close="showSettings = false"
      />
    </div>

    <!-- 字幕列表 -->
    <div ref="listRef" class="subtitle-list">
      <TransitionGroup name="subtitle">
        <div
          v-for="entry in displayEntries"
          :key="entry.id"
          class="subtitle-entry"
          :class="{ partial: entry.isPartial }"
        >
          <div class="entry-header" v-if="settings.showTimestamp">
            <span class="time">{{ formatTime(entry.timestamp) }}</span>
            <span class="lang-badge" v-if="entry.language">
              {{ entry.language }}
            </span>
          </div>
          <div class="entry-original">
            {{ entry.original }}
            <span v-if="entry.isPartial" class="typing-indicator">▍</span>
          </div>
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

<style scoped src="./SubtitleOverlay.scoped.css"></style>
