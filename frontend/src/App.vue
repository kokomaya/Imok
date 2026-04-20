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
const meetingStopping = ref(false);
const lastMeetingInfo = ref(null);

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
 * 启动时自动加载上次有内容的会议记录到 UI。
 */
async function autoLoadLastMeeting() {
  if (!window.electronAPI) return;
  try {
    const result = await window.electronAPI.listMeetings();
    if (!result.ok || !result.meetings?.length) return;
    const last = result.meetings[0];
    if (last.transcription_count <= 0) return;

    const loadResult = await window.electronAPI.loadMeeting(last.meeting_id);
    if (!loadResult.ok) return;

    const { transcriptions: trans, summaries } = loadResult.data;
    transcriptions.value = (trans || []).map((t, i) => ({
      id: i + 1,
      text: t.text,
      language: t.language || '',
      speaker: t.speaker || '',
      source: t.source || '',
      timestamp: t.timestamp
        ? new Date(t.timestamp * 1000).toLocaleTimeString()
        : '',
    }));

    summaryStore.clearAll();
    if (summaries?.segments) {
      for (const seg of summaries.segments) {
        summaryStore.addSegmentSummary(seg);
      }
    }
    if (summaries?.global_summary) {
      summaryStore.updateGlobalSummary({
        ...summaries.global_summary,
        action_items: summaries.action_items || [],
      });
    }
    summaryStore.markSaved();
    lastMeetingInfo.value = last;
  } catch (err) {
    console.error('[App] Failed to auto-load last meeting:', err);
  }
}

/**
 * 开始会议 — 如果已加载上次数据走继续/新建分支，否则直接新建。
 */
function startMeeting() {
  if (!window.electronAPI || meetingActive.value) return;
  if (!systemAudioEnabled.value && !micEnabled.value) {
    showError('请至少启用一个音频源（系统音频或麦克风）');
    return;
  }
  doStartNewMeeting();
}

/**
 * 开始全新会议 — 清空上次数据并启动。
 */
async function doStartNewMeeting() {
  if (!systemAudioEnabled.value && !micEnabled.value) {
    showError('请至少启用一个音频源（系统音频或麦克风）');
    return;
  }
  lastMeetingInfo.value = null;
  transcriptions.value = [];
  summaryStore.clearAll();
  loadedMeetingId.value = null;

  const source = getSourceType();
  await window.electronAPI.sendControl('switch_source', { source });
}

/**
 * 继续上次会议 — 数据已在 UI 中，直接传递 meeting_id 给后端启动。
 */
async function doContinueMeeting() {
  const mid = lastMeetingInfo.value?.meeting_id;
  lastMeetingInfo.value = null;

  if (!mid || !window.electronAPI) {
    doStartNewMeeting();
    return;
  }
  if (!systemAudioEnabled.value && !micEnabled.value) {
    showError('请至少启用一个音频源（系统音频或麦克风）');
    return;
  }
  loadedMeetingId.value = null;
  const source = getSourceType();
  await window.electronAPI.sendControl('switch_source', { source, meeting_id: mid });
}

/**
 * 停止会议 — 停止采集。
 */
async function stopMeeting() {
  if (!window.electronAPI || !meetingActive.value || meetingStopping.value) return;
  meetingStopping.value = true;
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
  status, meetingActive, meetingStopping, lastMeetingInfo, transcriptions,
  showError, handleMenuAction, syncAudioStateToMenu,
});

onMounted(async () => {
  if (currentRoute.value !== 'main') return;
  window.addEventListener('beforeunload', onBeforeUnload);
  await ipcListeners.setup();
  await autoLoadLastMeeting();
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
        <template v-if="!meetingActive && lastMeetingInfo">
          <button
            class="btn-meeting btn-start"
            @click="doContinueMeeting"
            :disabled="status === 'loading'"
            title="继续上次会议"
          >
            ▶ 继续会议
          </button>
          <button
            class="btn-meeting btn-new"
            @click="doStartNewMeeting"
            :disabled="status === 'loading'"
            title="开始新会议"
          >
            ✦ 新会议
          </button>
        </template>
        <button
          v-else-if="!meetingActive"
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
          :disabled="meetingStopping"
          title="停止会议录制"
        >
          {{ meetingStopping ? '⏳ 停止中…' : '⏹ 停止' }}
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
            :class="{ partial: item._isPartial }"
          >
            <span class="time">{{ item.timestamp }}</span>
            <span class="source-icon" v-if="item.source" :title="item.source === 'mic' ? '麦克风' : '系统音频'">{{ item.source === 'mic' ? '🎤' : '🔊' }}</span>
            <span class="speaker" v-if="item.speaker">[{{ item.speaker }}]</span>
            <span class="lang" v-if="item.language">[{{ item.language }}]</span>
            <span
              class="text"
              :contenteditable="!item._isPartial"
              @blur="onEditTranscription(item, $event)"
              @keydown.enter.prevent="$event.target.blur()"
            >{{ item.text }}<span v-if="item._isPartial" class="typing-cursor">▍</span></span>
          </div>
        </div>
      </section>
    </main>

  </div>
</template>

<style scoped src="./App.scoped.css"></style>
