<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue';
import { useHashRoute } from '@/router.js';
import { SubtitleOverlay } from '@/components/SubtitleOverlay';
import { MuteAssistPanel } from '@/components/MuteAssistPanel';
import { SummaryPanel } from '@/components/SummaryPanel';
import { muteAssistStore } from '@/stores/mute-assist-store.js';
import { summaryStore } from '@/stores/summary-store.js';
import { expressionService } from '@/services/expression-service.js';

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
const historyMeetings = ref([]);
const historyLoading = ref(false);
const loadedMeetingId = ref(null);

// ── 自动滚动 ──
const transcriptionListRef = ref(null);
function scrollToBottom() {
  nextTick(() => {
    const el = transcriptionListRef.value;
    if (el) el.scrollTop = el.scrollHeight;
  });
}
watch(transcriptions, scrollToBottom, { deep: true });

let cleanupFns = [];

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
  if (!meetingActive.value) return;
  if (!systemAudioEnabled.value && !micEnabled.value) {
    showError('至少需要一个音频源，已自动恢复系统音频');
    systemAudioEnabled.value = true;
    return;
  }
  const source = getSourceType();
  await window.electronAPI.sendControl('switch_source', { source });
}

onMounted(async () => {
  if (currentRoute.value !== 'main') return;

  window.addEventListener('beforeunload', onBeforeUnload);

  if (!window.electronAPI) {
    status.value = 'no-electron';
    return;
  }

  cleanupFns.push(
    window.electronAPI.on('python:status', async (data) => {
      const prevActive = meetingActive.value;
      status.value = data.state || 'unknown';
      meetingActive.value = data.state === 'running';

      // 会议启动 → 记录 meeting_id 到 summaryStore
      if (data.state === 'running' && data.meeting_id) {
        summaryStore.setLiveMeetingId(data.meeting_id);
      }

      // 会议停止 → 自动保存前端摘要到该会议
      if (data.state === 'stopped' && prevActive) {
        const mid = data.meeting_id || summaryStore.state.liveMeetingId;
        if (mid && summaryStore.hasSummaryContent.value && window.electronAPI?.saveMeetingSummaries) {
          try {
            const saveData = summaryStore.getSummariesForSave();
            await window.electronAPI.saveMeetingSummaries(mid, saveData);
            summaryStore.markSaved();
          } catch (err) {
            console.error('[App] Auto-save summaries failed:', err);
          }
        }
        summaryStore.setLiveMeetingId('');
      }
    }),
  );

  cleanupFns.push(
    window.electronAPI.on('python:transcription', (data) => {
      transcriptions.value.push({
        id: Date.now(),
        text: data.text,
        language: data.language || '',
        speaker: data.speaker || '',
        timestamp: new Date().toLocaleTimeString(),
      });
      // 同步到 summaryStore 供前端降级生成摘要使用
      summaryStore.addLiveTranscription({
        text: data.text,
        timestamp: Date.now() / 1000,
      });
    }),
  );

  cleanupFns.push(
    window.electronAPI.on('python:error', (data) => {
      console.error('[Python Error]', data);
      showError(data.message || data.code || '后端错误');
    }),
  );

  cleanupFns.push(
    window.electronAPI.on('python:segment-summary', (data) => {
      summaryStore.addSegmentSummary(data);
    }),
  );

  cleanupFns.push(
    window.electronAPI.on('python:global-summary', (data) => {
      summaryStore.updateGlobalSummary(data);
    }),
  );

  cleanupFns.push(
    window.electronAPI.on('mute-panel:toggle', () => {
      muteAssistStore.toggleVisible();
    }),
  );

  // 初始化 LLM 配置 → 表达服务
  try {
    const result = await window.electronAPI.getLLMConfig();
    if (result.ok && result.config) {
      expressionService.init(result.config);
    }
  } catch (err) {
    console.error('[App] Failed to load LLM config:', err);
  }
});

onUnmounted(() => {
  cleanupFns.forEach((fn) => fn());
  cleanupFns = [];
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

// ── 会议历史方法 ──

/**
 * 检查当前摘要是否有未保存修改，提示用户保存。
 * @returns {Promise<boolean>} true = 可以继续操作，false = 用户取消
 */
async function checkUnsavedSummary() {
  const meetingId = summaryStore.activeMeetingId.value;
  if (!summaryStore.isDirty.value || !meetingId) return true;

  const action = window.confirm(
    '当前摘要有未保存的修改，是否保存？\n\n点击"确定"保存后继续，点击"取消"放弃修改。',
  );
  if (action) {
    // 保存
    if (window.electronAPI?.saveMeetingSummaries) {
      const data = summaryStore.getSummariesForSave();
      const result = await window.electronAPI.saveMeetingSummaries(meetingId, data);
      if (result.ok) {
        summaryStore.markSaved();
      } else {
        window.alert('保存失败：' + (result.error || '未知错误'));
        return false;
      }
    }
  }
  // 无论是否保存，都允许继续
  return true;
}

async function toggleHistory() {
  historyVisible.value = !historyVisible.value;
  if (historyVisible.value) {
    await refreshMeetingList();
  }
}

async function refreshMeetingList() {
  if (!window.electronAPI?.listMeetings) return;
  historyLoading.value = true;
  try {
    const result = await window.electronAPI.listMeetings();
    if (result.ok) {
      historyMeetings.value = result.meetings;
    }
  } catch (err) {
    console.error('[App] Failed to list meetings:', err);
  } finally {
    historyLoading.value = false;
  }
}

async function loadMeeting(meetingId) {
  if (!window.electronAPI?.loadMeeting) return;

  // 检查未保存的摘要修改
  if (!(await checkUnsavedSummary())) return;

  historyLoading.value = true;
  try {
    const result = await window.electronAPI.loadMeeting(meetingId);
    if (!result.ok) {
      console.error('[App] Failed to load meeting:', result.error);
      return;
    }
    const { meta, transcriptions: trans, summaries } = result.data;

    // 填充字幕列表
    transcriptions.value = (trans || []).map((t, i) => ({
      id: i + 1,
      text: t.text,
      language: t.language || '',
      speaker: t.speaker || '',
      timestamp: t.timestamp
        ? new Date(t.timestamp * 1000).toLocaleTimeString()
        : '',
    }));

    // 填充摘要 store
    summaryStore.clearAll();

    // 设置回看模式（传递原始转写文本供 SummaryPanel 调用 LLM 生成摘要）
    summaryStore.setReviewData(
      (trans || []).map((t) => ({ text: t.text, timestamp: t.timestamp || 0 })),
      meetingId,
    );

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

    // 标记初始加载状态为已保存
    summaryStore.markSaved();

    // 展开摘要面板
    if (!summaryStore.state.visible) {
      summaryStore.toggleVisible();
    }

    loadedMeetingId.value = meetingId;
    historyVisible.value = false;
  } catch (err) {
    console.error('[App] Failed to load meeting:', err);
  } finally {
    historyLoading.value = false;
  }
}

async function deleteMeeting(meetingId) {
  if (!window.electronAPI?.deleteMeeting) return;
  if (!window.confirm('确定要删除此会议记录？此操作不可恢复。')) return;
  try {
    const result = await window.electronAPI.deleteMeeting(meetingId);
    if (result.ok) {
      historyMeetings.value = historyMeetings.value.filter(
        (m) => m.meeting_id !== meetingId,
      );
      if (loadedMeetingId.value === meetingId) {
        loadedMeetingId.value = null;
        transcriptions.value = [];
        summaryStore.clearAll();
      }
    }
  } catch (err) {
    console.error('[App] Failed to delete meeting:', err);
  }
}

async function backToLive() {
  if (!(await checkUnsavedSummary())) return;
  loadedMeetingId.value = null;
  transcriptions.value = [];
  summaryStore.clearAll();  // clearAll already resets reviewMode
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

function formatMeetingTime(epoch) {
  if (!epoch) return '';
  const d = new Date(epoch * 1000);
  return d.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

function formatDuration(start, end) {
  if (!start) return '';
  const endTs = end || Date.now() / 1000;
  const sec = Math.round(endTs - start);
  if (sec < 60) return `${sec}秒`;
  const min = Math.round(sec / 60);
  return `${min}分钟`;
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
    <div v-if="historyVisible" class="history-panel">
      <div class="history-header">
        <span class="history-title">📂 历史会议</span>
        <button class="history-close" @click="historyVisible = false">✕</button>
      </div>
      <div v-if="historyLoading" class="history-loading">加载中…</div>
      <div v-else-if="historyMeetings.length === 0" class="history-empty">
        暂无历史会议记录
      </div>
      <div v-else class="history-list">
        <div
          v-for="m in historyMeetings"
          :key="m.meeting_id"
          class="history-item"
          :class="{ active: loadedMeetingId === m.meeting_id }"
        >
          <div class="history-item-main" @click="loadMeeting(m.meeting_id)">
            <div class="history-item-top">
              <span class="history-date">{{ formatMeetingTime(m.started_at) }}</span>
              <span class="history-status" :class="m.status">{{ m.status === 'running' ? '进行中' : '已结束' }}</span>
            </div>
            <div class="history-item-meta">
              <span class="meta-tag">💬 {{ m.transcription_count || 0 }} 条</span>
              <span v-if="m.has_summary" class="meta-tag summary-tag">📋 摘要</span>
              <span class="meta-tag">⏱ {{ formatDuration(m.started_at, m.ended_at) }}</span>
            </div>
          </div>
          <button
            class="history-delete"
            @click.stop="deleteMeeting(m.meeting_id)"
            title="删除此会议记录"
          >🗑</button>
        </div>
      </div>
    </div>

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

/* ── 历史面板 ── */

.history-panel {
  border-bottom: 1px solid #e0e0e0;
  background: #fafafa;
  max-height: 280px;
  overflow-y: auto;
}

.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  border-bottom: 1px solid #eee;
  position: sticky;
  top: 0;
  background: #fafafa;
  z-index: 1;
}

.history-title {
  font-size: 13px;
  font-weight: 600;
}

.history-close {
  border: none;
  background: none;
  font-size: 16px;
  cursor: pointer;
  color: #999;
  padding: 2px 6px;
}

.history-close:hover {
  color: #333;
}

.history-loading,
.history-empty {
  padding: 20px 12px;
  text-align: center;
  color: #999;
  font-size: 13px;
}

.history-list {
  padding: 2px 0;
}

.history-item {
  display: flex;
  align-items: center;
  padding: 6px 12px;
  border-bottom: 1px solid #f0f0f0;
  transition: background 0.15s;
}

.history-item:hover {
  background: #e8eaf6;
}

.history-item.active {
  background: #e3f2fd;
  border-left: 3px solid #1565c0;
}

.history-item-main {
  flex: 1;
  cursor: pointer;
  min-width: 0;
}

.history-item-top {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}

.history-date {
  font-size: 12px;
  font-weight: 500;
  color: #333;
}

.history-status {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  background: #e0e0e0;
  color: #666;
}

.history-status.running {
  background: #e3f2fd;
  color: #1565c0;
}

.history-status.finished {
  background: #e8f5e9;
  color: #2e7d32;
}

.history-item-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.meta-tag {
  font-size: 10px;
  color: #888;
}

.summary-tag {
  color: #2e7d32;
  font-weight: 500;
}

.history-delete {
  border: none;
  background: none;
  font-size: 14px;
  cursor: pointer;
  padding: 4px 6px;
  opacity: 0.3;
  transition: opacity 0.15s;
}

.history-delete:hover {
  opacity: 1;
}
</style>
