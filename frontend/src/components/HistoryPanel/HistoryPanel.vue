<script setup>
/**
 * 历史会议列表面板 — 从 App.vue 提取。
 */
import { ref } from 'vue';

const props = defineProps({
  visible: Boolean,
  loadedMeetingId: String,
});

const emit = defineEmits(['close', 'load', 'delete']);

const meetings = ref([]);
const loading = ref(false);

async function refresh() {
  if (!window.electronAPI?.listMeetings) return;
  loading.value = true;
  try {
    const result = await window.electronAPI.listMeetings();
    if (result.ok) {
      meetings.value = result.meetings;
    }
  } catch (err) {
    console.error('[HistoryPanel] Failed to list meetings:', err);
  } finally {
    loading.value = false;
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
  if (!end) return '—';  // 未正常结束的会议不显示虚假时长
  const sec = Math.round(end - start);
  if (sec < 60) return `${sec}秒`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}分钟`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return `${h}小时${m > 0 ? m + '分钟' : ''}`;
}

function displayTitle(meeting) {
  return meeting.title || meeting.meeting_id || '未命名会议';
}

function statusText(meeting) {
  if (meeting.status === 'running' && !meeting.ended_at) return '未结束';
  if (meeting.status === 'running') return '进行中';
  return '已结束';
}

defineExpose({ refresh });
</script>

<template>
  <div v-if="visible" class="history-panel">
    <div class="history-header">
      <span class="history-title">📂 历史会议</span>
      <button class="history-close" @click="$emit('close')">✕</button>
    </div>
    <div v-if="loading" class="history-loading">加载中…</div>
    <div v-else-if="meetings.length === 0" class="history-empty">
      暂无历史会议记录
    </div>
    <div v-else class="history-list">
      <div
        v-for="m in meetings"
        :key="m.meeting_id"
        class="history-item"
        :class="{ active: loadedMeetingId === m.meeting_id }"
      >
        <div class="history-item-main" @click="$emit('load', m.meeting_id)">
          <div class="history-item-top">
            <span class="history-name">{{ displayTitle(m) }}</span>
            <span class="history-date">{{ formatMeetingTime(m.started_at) }}</span>
            <span class="history-status" :class="m.status">{{ statusText(m) }}</span>
          </div>
          <div class="history-item-meta">
            <span class="meta-tag">💬 {{ m.transcription_count || 0 }} 条</span>
            <span v-if="m.has_summary" class="meta-tag summary-tag">📋 摘要</span>
            <span class="meta-tag">⏱ {{ formatDuration(m.started_at, m.ended_at) }}</span>
          </div>
        </div>
        <button
          class="history-delete"
          @click.stop="$emit('delete', m.meeting_id)"
          title="删除此会议记录"
        >🗑</button>
      </div>
    </div>
  </div>
</template>

<style scoped src="./HistoryPanel.scoped.css"></style>
