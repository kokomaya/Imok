<script setup>
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue';
import { useHashRoute } from '@/router.js';
import { SubtitleOverlay } from '@/components/SubtitleOverlay';
import { MuteAssistPanel } from '@/components/MuteAssistPanel';
import { SummaryPanel } from '@/components/SummaryPanel';
import { AudioDevicePanel } from '@/components/AudioDevicePanel';
import { HistoryPanel } from '@/components/HistoryPanel';
import { muteAssistStore } from '@/stores/mute-assist-store.js';
import { summaryStore } from '@/stores/summary-store.js';
import { useMeetingHistory } from '@/composables/useMeetingHistory.js';
import { useIPCListeners } from '@/composables/useIPCListeners.js';

const { currentRoute } = useHashRoute();

const status = ref('disconnected');
const transcriptions = ref([]);

// ── 音频源独立开关 ──
const systemAudioEnabled = ref(true);
const micEnabled = ref(true);

// ── 会议控制 ──
const meetingActive = ref(false);

// ── 错误通知 ──
const errorMessage = ref('');
let errorTimer = null;

function showError(msg) {
  errorMessage.value = msg;
  if (errorTimer) clearTimeout(errorTimer);
  errorTimer = setTimeout(() => { errorMessage.value = ''; }, 8000);
}
function dismissError() {
  errorMessage.value = '';
  if (errorTimer) clearTimeout(errorTimer);
}

// ── 会议历史 ──
const historyVisible = ref(false);
const devicePanelVisible = ref(false);
const historyPanelRef = ref(null);
const loadedMeetingId = ref(null);

const { checkUnsavedSummary, toggleHistory, loadMeeting, deleteMeeting, backToLive } = useMeetingHistory({
  transcriptions, historyVisible, historyPanelRef, loadedMeetingId,
});

// ── 自动滚动 ──
const transcriptionListRef = ref(null);
function scrollToBottom() {
  nextTick(() => {
    const el = transcriptionListRef.value;
    if (el) el.scrollTop = el.scrollHeight;
  });
}
watch(transcriptions, scrollToBottom, { deep: true });

/**
 * 根据当前音频开关计算 source_type 参数。
 */
function getSourceType() {
  if (systemAudioEnabled.value && micEnabled.value) return 'both';
  if (systemAudioEnabled.value) return 'wasapi';
  if (micEnabled.value) return 'mic';
  return 'wasapi'; // 至少保留一个
}

/**
 * 开始会议 — 启动音频采集和 ASR。
 */
async function startMeeting() {
  if (!window.electronAPI || meetingActive.value) return;
  // 至少启用一个音频源
  if (!systemAudioEnabled.value && !micEnabled.value) {
    showError('请至少启用一个音频源（系统音频或麦克风）');
    return;
  }
  // 清空上一次会议的残留数据，避免新旧数据混合
  transcriptions.value = [];
  summaryStore.clearAll();

  const source = getSourceType();
  // 先切换音频源配置，再启动
  await window.electronAPI.sendControl('switch_source', { source });
  await window.electronAPI.sendControl('start');
}

/**
 * 停止会议 — 停止采集。
 */
async function stopMeeting() {
  if (!window.electronAPI || !meetingActive.value) return;
  await window.electronAPI.sendControl('stop');
}

/**
 * 音频源开关变化时，如果会议正在进行，重新配置。
 */
async function onAudioToggle() {
  if (!meetingActive.value) {
    syncAudioStateToMenu();
    return;
  }
  if (!systemAudioEnabled.value && !micEnabled.value) {
    showError('至少需要一个音频源，已自动恢复系统音频');
    systemAudioEnabled.value = true;
    syncAudioStateToMenu();
    return;
  }
  const source = getSourceType();
  await window.electronAPI.sendControl('switch_source', { source });
  syncAudioStateToMenu();
}

// ── 菜单栏 ↔ 工具栏同步 ──

function syncAudioStateToMenu() {
  window.electronAPI?.syncAudioState?.({
    systemAudio: systemAudioEnabled.value,
    mic: micEnabled.value,
  });
}

function handleMenuAction(action, data) {
  switch (action) {
    case 'start-meeting':
      startMeeting();
      break;
    case 'stop-meeting':
      stopMeeting();
      break;
    case 'toggle-history':
      toggleHistory();
      break;
    case 'enable-system-audio':
      systemAudioEnabled.value = true;
      onAudioToggle();
      syncAudioStateToMenu();
      break;
    case 'disable-system-audio':
      systemAudioEnabled.value = false;
      onAudioToggle();
      syncAudioStateToMenu();
      break;
    case 'enable-mic':
      micEnabled.value = true;
      onAudioToggle();
      syncAudioStateToMenu();
      break;
    case 'disable-mic':
      micEnabled.value = false;
      onAudioToggle();
      syncAudioStateToMenu();
      break;
    case 'open-overlay':
      openOverlay();
      break;
    case 'toggle-mute-assist':
      muteAssistStore.toggleVisible();
      break;
    case 'toggle-summary':
      summaryStore.toggleVisible();
      break;
    case 'clear-transcriptions':
      clearTranscriptions();
      break;
    case 'device-testing':
      status.value = `测试 ${data?.type === 'loopback' ? '系统音频' : '麦克风'} 设备…`;
      break;
    case 'device-test-result':
      status.value = meetingActive.value ? 'running' : 'ready';
      break;
    case 'device-changed':
      // 设备已在 main.js 侧更新，仅做 UI 反馈
      break;
    case 'toggle-device-panel':
      devicePanelVisible.value = !devicePanelVisible.value;
      break;
  }
}

const ipcListeners = useIPCListeners({
  status, meetingActive, transcriptions,
  showError, handleMenuAction, syncAudioStateToMenu,
});

onMounted(async () => {
  if (currentRoute.value !== 'main') return;
  window.addEventListener('beforeunload', onBeforeUnload);
  await ipcListeners.setup();
});

onUnmounted(() => {
  ipcListeners.cleanup();
  window.removeEventListener('beforeunload', onBeforeUnload);
});

function onBeforeUnload(e) {
  if (summaryStore.isDirty.value && summaryStore.activeMeetingId.value) {
    e.preventDefault();
    e.returnValue = '';
  }
}

function openOverlay() {
  if (window.electronAPI) {
    window.electronAPI.openOverlay();
  }
}

function clearTranscriptions() {
  transcriptions.value = [];
}

function onEditTranscription(item, event) {
  const newText = event.target.textContent.trim();
  if (newText !== item.text) {
    item.text = newText;
  }
}
</script>

<template>
  <!-- 悬浮窗视图 -->
  <SubtitleOverlay v-if="currentRoute === 'overlay'" />

  <!-- 主窗口视图 -->
  <div v-else class="app">
    <header class="header">
      <h1 class="title">Imok</h1>
      <div class="header-actions">
        <!-- 会议开始/停止按钮 -->
        <button
          v-if="!meetingActive"
          class="btn-meeting btn-start"
          @click="startMeeting"
          :disabled="status === 'loading'"
          title="开始会议录制"
        >
          ▶ 开始会议
        </button>
        <button
          v-else
          class="btn-meeting btn-stop"
          @click="stopMeeting"
          title="停止会议录制"
        >
          ⏹ 停止
        </button>

        <!-- 音频源独立开关 -->
        <label class="audio-toggle" :class="{ active: systemAudioEnabled }" title="系统音频（Teams/Zoom 等）">
          <input type="checkbox" v-model="systemAudioEnabled" @change="onAudioToggle" />
          🔊
        </label>
        <label class="audio-toggle" :class="{ active: micEnabled }" title="麦克风">
          <input type="checkbox" v-model="micEnabled" @change="onAudioToggle" />
          🎤
        </label>

        <button
          class="btn-icon"
          :class="{ active: devicePanelVisible }"
          @click="devicePanelVisible = !devicePanelVisible"
          title="音频设备监控"
        >🎛</button>

        <span class="header-sep"></span>

        <!-- 功能按钮 -->
        <button class="btn-icon" @click="openOverlay" title="打开悬浮字幕">🎯</button>
        <button class="btn-icon" @click="muteAssistStore.toggleVisible()" title="闭麦表达助手 (Ctrl+Shift+M)">💬</button>
        <button class="btn-icon" @click="summaryStore.toggleVisible()" title="会议摘要面板">📝</button>
        <button
          class="btn-icon"
          :class="{ active: historyVisible }"
          @click="toggleHistory"
          title="查看历史会议记录"
        >📂</button>

        <span class="status-badge" :class="status">{{ status }}</span>
      </div>
    </header>

    <!-- 错误通知条 -->
    <div v-if="errorMessage" class="error-bar">
      <span>⚠ {{ errorMessage }}</span>
      <button class="error-dismiss" @click="dismissError">✕</button>
    </div>

    <!-- 回看提示条 -->
    <div v-if="loadedMeetingId" class="review-bar">
      <span>📖 正在回看历史会议: {{ loadedMeetingId }}</span>
      <button class="btn-back-live" @click="backToLive">返回实时</button>
    </div>

    <!-- 历史会议面板 -->
    <HistoryPanel
      ref="historyPanelRef"
      :visible="historyVisible"
      :loaded-meeting-id="loadedMeetingId"
      @close="historyVisible = false"
      @load="loadMeeting"
      @delete="deleteMeeting"
    />

    <!-- 音频设备监控面板 -->
    <AudioDevicePanel
      :visible="devicePanelVisible"
      @close="devicePanelVisible = false"
    />

    <main class="content">
      <!-- 会议摘要面板 -->
      <SummaryPanel />

      <!-- 闭麦表达助手 -->
      <MuteAssistPanel />

      <section class="transcription-panel">
        <div class="transcription-header">
          <h2>实时字幕</h2>
          <button
            v-if="transcriptions.length > 0"
            class="btn-clear-transcriptions"
            @click="clearTranscriptions"
            title="清空当前字幕"
          >
            🗑 清空
          </button>
        </div>
        <div class="transcription-list" ref="transcriptionListRef">
          <p v-if="transcriptions.length === 0" class="placeholder">
            {{ meetingActive ? '等待语音输入…' : '点击「开始会议」启动录制' }}
          </p>
          <div
            v-for="item in transcriptions"
            :key="item.id"
            class="transcription-item"
          >
            <span class="time">{{ item.timestamp }}</span>
            <span class="source-icon" v-if="item.source" :title="item.source === 'mic' ? '麦克风' : '系统音频'">{{ item.source === 'mic' ? '🎤' : '🔊' }}</span>
            <span class="speaker" v-if="item.speaker">[{{ item.speaker }}]</span>
            <span class="lang" v-if="item.language">[{{ item.language }}]</span>
            <span
              class="text"
              contenteditable="true"
              @blur="onEditTranscription(item, $event)"
              @keydown.enter.prevent="$event.target.blur()"
            >{{ item.text }}</span>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

/* ── Header ── */

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid #e0e0e0;
  background: #fafafa;
  gap: 8px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.title {
  font-size: 15px;
  font-weight: 700;
  margin: 0;
  flex-shrink: 0;
  color: #333;
}

.header-sep {
  width: 1px;
  height: 20px;
  background: #ddd;
  margin: 0 2px;
}

/* ── 会议开始/停止按钮 ── */

.btn-meeting {
  font-size: 12px;
  font-weight: 600;
  padding: 4px 12px;
  border-radius: 4px;
  cursor: pointer;
  border: none;
}

.btn-start {
  background: #4caf50;
  color: #fff;
}

.btn-start:hover:not(:disabled) {
  background: #388e3c;
}

.btn-start:disabled {
  background: #a5d6a7;
  cursor: not-allowed;
}

.btn-stop {
  background: #ef5350;
  color: #fff;
}

.btn-stop:hover {
  background: #c62828;
}

/* ── 音频源开关 ── */

.audio-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 28px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 16px;
  background: #f0f0f0;
  border: 1px solid #ddd;
  opacity: 0.45;
  transition: all 0.15s;
  user-select: none;
}

.audio-toggle input {
  display: none;
}

.audio-toggle.active {
  opacity: 1;
  background: #e3f2fd;
  border-color: #90caf9;
}

.audio-toggle:hover {
  opacity: 0.85;
  border-color: #bbb;
}

/* ── 图标按钮 ── */

.btn-icon {
  width: 32px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #ddd;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  font-size: 14px;
  padding: 0;
  transition: all 0.15s;
}

.btn-icon:hover {
  background: #e8e8e8;
  border-color: #bbb;
}

.btn-icon.active {
  background: #e8eaf6;
  border-color: #9575cd;
}

/* ── 状态徽章 ── */

.status-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  background: #e0e0e0;
  color: #666;
  flex-shrink: 0;
}

.status-badge.ready {
  background: #e8f5e9;
  color: #2e7d32;
}

.status-badge.running {
  background: #e3f2fd;
  color: #1565c0;
}

.status-badge.loading {
  background: #fff3e0;
  color: #e65100;
}

.status-badge.stopped {
  background: #fafafa;
  color: #999;
}

.status-badge.error {
  background: #ffebee;
  color: #c62828;
}

/* ── 错误通知条 ── */

.error-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: #ffebee;
  border-bottom: 1px solid #ef9a9a;
  font-size: 13px;
  color: #c62828;
}

.error-dismiss {
  border: none;
  background: none;
  font-size: 14px;
  cursor: pointer;
  color: #c62828;
  padding: 2px 6px;
}

.error-dismiss:hover {
  background: rgba(0,0,0,0.08);
  border-radius: 4px;
}

/* ── 回看提示条 ── */

.review-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: #fff3e0;
  border-bottom: 1px solid #ffe0b2;
  font-size: 13px;
  color: #e65100;
}

.btn-back-live {
  font-size: 12px;
  padding: 2px 10px;
  border: 1px solid #e65100;
  border-radius: 4px;
  background: #fff;
  color: #e65100;
  cursor: pointer;
}

.btn-back-live:hover {
  background: #e65100;
  color: #fff;
}

/* ── 主内容区 ── */

.content {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
}

.transcription-panel h2 {
  font-size: 14px;
  color: #555;
  margin: 0;
}

.transcription-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.btn-clear-transcriptions {
  font-size: 11px;
  padding: 2px 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  background: #fff;
  color: #666;
  cursor: pointer;
  transition: all 0.15s;
}

.btn-clear-transcriptions:hover {
  background: #ffebee;
  border-color: #ef9a9a;
  color: #c62828;
}

.transcription-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: calc(100vh - 180px);
  overflow-y: auto;
}

.placeholder {
  color: #999;
  font-style: italic;
  text-align: center;
  padding: 32px 0;
}

.transcription-item {
  display: flex;
  gap: 8px;
  font-size: 13px;
  line-height: 1.5;
}

.time {
  color: #999;
  font-size: 11px;
  flex-shrink: 0;
}

.lang {
  color: #1565c0;
  font-size: 11px;
  flex-shrink: 0;
}

.speaker {
  color: #6a1b9a;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
}

.source-icon {
  font-size: 12px;
  flex-shrink: 0;
  cursor: default;
}

.text {
  color: #333;
  outline: none;
  border-radius: 2px;
  padding: 0 2px;
  min-width: 20px;
  cursor: text;
}

.text:hover {
  background: #f5f5f5;
}

.text:focus {
  background: #e3f2fd;
  box-shadow: 0 0 0 1px #90caf9;
}
</style>
