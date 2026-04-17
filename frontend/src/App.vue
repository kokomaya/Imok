<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
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
const audioSource = ref('wasapi');

// ── 会议历史 ──
const historyVisible = ref(false);
const historyMeetings = ref([]);
const historyLoading = ref(false);
const loadedMeetingId = ref(null);

let cleanupFns = [];

onMounted(async () => {
  // 主窗口视图才直接监听 IPC（overlay 有独立的 ipc-bridge 服务）
  if (currentRoute.value !== 'main') return;

  // 关闭窗口时检查未保存摘要
  window.addEventListener('beforeunload', onBeforeUnload);

  if (!window.electronAPI) {
    status.value = 'no-electron';
    return;
  }

  cleanupFns.push(
    window.electronAPI.on('python:status', (data) => {
      status.value = data.state || 'unknown';
      if (data.source) {
        audioSource.value = data.source;
      }
    }),
  );

  cleanupFns.push(
    window.electronAPI.on('python:transcription', (data) => {
      transcriptions.value.push({
        id: Date.now(),
        text: data.text,
        language: data.language || '',
        timestamp: new Date().toLocaleTimeString(),
      });
    }),
  );

  cleanupFns.push(
    window.electronAPI.on('python:error', (data) => {
      console.error('[Python Error]', data);
    }),
  );

  // 段落摘要 → summary store
  cleanupFns.push(
    window.electronAPI.on('python:segment-summary', (data) => {
      summaryStore.addSegmentSummary(data);
    }),
  );

  // 全局摘要 → summary store
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
      console.log('[App] Expression service initialized');
    } else {
      console.warn('[App] LLM config not available:', result.error);
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
  if (summaryStore.isDirty.value && summaryStore.state.reviewMeetingId) {
    e.preventDefault();
    e.returnValue = '';
  }
}

function openOverlay() {
  if (window.electronAPI) {
    window.electronAPI.openOverlay();
  }
}

async function switchAudioSource() {
  if (!window.electronAPI) return;
  const next = audioSource.value === 'wasapi' ? 'mic' : 'wasapi';
  await window.electronAPI.sendControl('switch_source', { source: next });
}

// ── 会议历史方法 ──

/**
 * 检查当前摘要是否有未保存修改，提示用户保存。
 * @returns {Promise<boolean>} true = 可以继续操作，false = 用户取消
 */
async function checkUnsavedSummary() {
  if (!summaryStore.isDirty.value || !summaryStore.state.reviewMeetingId) return true;

  const action = window.confirm(
    '当前摘要有未保存的修改，是否保存？\n\n点击"确定"保存后继续，点击"取消"放弃修改。',
  );
  if (action) {
    // 保存
    const meetingId = summaryStore.state.reviewMeetingId;
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
  try {
    const result = await window.electronAPI.deleteMeeting(meetingId);
    if (result.ok) {
      historyMeetings.value = historyMeetings.value.filter(
        (m) => m.meeting_id !== meetingId,
      );
      if (loadedMeetingId.value === meetingId) {
        loadedMeetingId.value = null;
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
      <h1 class="title">Imok Meeting Assistant</h1>
      <div class="header-actions">
        <button
          class="btn-audio-source"
          :class="audioSource"
          @click="switchAudioSource"
          :title="audioSource === 'wasapi' ? '当前：系统音频（点击切换到麦克风）' : '当前：麦克风（点击切换到系统音频）'"
        >
          {{ audioSource === 'wasapi' ? '🔊 系统音频' : '🎤 麦克风' }}
        </button>
        <button class="btn-overlay" @click="openOverlay" title="打开悬浮字幕">
          字幕窗
        </button>
        <button
          class="btn-mute-panel"
          @click="muteAssistStore.toggleVisible()"
          title="闭麦表达助手 (Ctrl+Shift+M)"
        >
          闭麦助手
        </button>
        <button
          class="btn-summary"
          @click="summaryStore.toggleVisible()"
          title="会议摘要面板"
        >
          摘要
        </button>
        <button
          class="btn-history"
          :class="{ active: historyVisible }"
          @click="toggleHistory"
          title="查看历史会议记录"
        >
          📂 历史
        </button>
        <span class="status-badge" :class="status">{{ status }}</span>
      </div>
    </header>

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
              <span v-if="m.audio_source" class="meta-tag">{{ m.audio_source === 'wasapi' ? '🔊 系统音频' : '🎤 麦克风' }}</span>
              <span class="meta-tag">💬 {{ m.transcription_count || 0 }} 条字幕</span>
              <span v-if="m.has_summary" class="meta-tag summary-tag">📋 有摘要</span>
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
        <h2>实时字幕</h2>
        <div class="transcription-list">
          <p v-if="transcriptions.length === 0" class="placeholder">
            等待语音输入…
          </p>
          <div
            v-for="item in transcriptions"
            :key="item.id"
            class="transcription-item"
          >
            <span class="time">{{ item.timestamp }}</span>
            <span class="lang" v-if="item.language">[{{ item.language }}]</span>
            <span class="text">{{ item.text }}</span>
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

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #e0e0e0;
  background: #fafafa;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.btn-overlay {
  font-size: 12px;
  padding: 4px 10px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  color: #333;
  cursor: pointer;
}

.btn-overlay:hover {
  background: #e3f2fd;
  border-color: #90caf9;
}

.btn-mute-panel {
  font-size: 12px;
  padding: 4px 10px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  color: #333;
  cursor: pointer;
}

.btn-mute-panel:hover {
  background: #fff3e0;
  border-color: #ffb74d;
}

.btn-summary {
  font-size: 12px;
  padding: 4px 10px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  color: #333;
  cursor: pointer;
}

.btn-summary:hover {
  background: #e8f5e9;
  border-color: #81c784;
}

.btn-audio-source {
  font-size: 12px;
  padding: 4px 10px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  color: #333;
  cursor: pointer;
}

.btn-audio-source.wasapi {
  background: #e8eaf6;
  border-color: #7986cb;
  color: #283593;
}

.btn-audio-source.mic {
  background: #fce4ec;
  border-color: #f48fb1;
  color: #880e4f;
}

.title {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
}

.status-badge {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  background: #e0e0e0;
  color: #666;
}

.status-badge.ready {
  background: #e8f5e9;
  color: #2e7d32;
}

.status-badge.running {
  background: #e3f2fd;
  color: #1565c0;
}

.status-badge.error {
  background: #ffebee;
  color: #c62828;
}

.content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.transcription-panel h2 {
  font-size: 14px;
  color: #555;
  margin: 0 0 12px 0;
}

.transcription-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.placeholder {
  color: #999;
  font-style: italic;
}

.transcription-item {
  display: flex;
  gap: 8px;
  font-size: 14px;
  line-height: 1.5;
}

.time {
  color: #999;
  font-size: 12px;
  flex-shrink: 0;
}

.lang {
  color: #1565c0;
  font-size: 12px;
  flex-shrink: 0;
}

.text {
  color: #333;
}

/* ── 历史会议按钮 ── */

.btn-history {
  font-size: 12px;
  padding: 4px 10px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  color: #333;
  cursor: pointer;
}

.btn-history:hover,
.btn-history.active {
  background: #ede7f6;
  border-color: #9575cd;
  color: #4527a0;
}

/* ── 回看提示条 ── */

.review-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 16px;
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

/* ── 历史面板 ── */

.history-panel {
  border-bottom: 1px solid #e0e0e0;
  background: #fafafa;
  max-height: 320px;
  overflow-y: auto;
}

.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  border-bottom: 1px solid #eee;
  position: sticky;
  top: 0;
  background: #fafafa;
  z-index: 1;
}

.history-title {
  font-size: 14px;
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
  padding: 24px 16px;
  text-align: center;
  color: #999;
  font-size: 13px;
}

.history-list {
  padding: 4px 0;
}

.history-item {
  display: flex;
  align-items: center;
  padding: 8px 16px;
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
  margin-bottom: 4px;
}

.history-date {
  font-size: 13px;
  font-weight: 500;
  color: #333;
}

.history-status {
  font-size: 11px;
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
  font-size: 11px;
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
  opacity: 0.4;
  transition: opacity 0.15s;
}

.history-delete:hover {
  opacity: 1;
}
</style>
