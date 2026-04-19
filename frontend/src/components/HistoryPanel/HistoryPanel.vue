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
  const endTs = end || Date.now() / 1000;
  const sec = Math.round(endTs - start);
  if (sec < 60) return `${sec}秒`;
  const min = Math.round(sec / 60);
  return `${min}分钟`;
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
          @click.stop="$emit('delete', m.meeting_id)"
          title="删除此会议记录"
        >🗑</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
