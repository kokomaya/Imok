<script setup>
/**
 * 会议摘要面板。
 *
 * 单一职责：展示会议摘要的 UI（主题、结论、Action Items、时间线）。
 * 数据来源：summaryStore（由 ipc-bridge 从 Python 子进程接收推送）。
 * 回看模式：直接通过 llm:chat 代理调用 LLM 生成摘要。
 */

import { ref, computed, watch, nextTick } from 'vue';
import { summaryStore } from '@/stores/summary-store.js';

const triggeringSegment = ref(false);
const triggeringGlobal = ref(false);
const saving = ref(false);
const summaryInterval = ref(60);

const INTERVAL_OPTIONS = [
  { label: '1分钟', value: 60 },
  { label: '2分钟', value: 120 },
  { label: '3分钟', value: 180 },
  { label: '5分钟', value: 300 },
  { label: '10分钟', value: 600 },
];

// ── LLM Prompts（与 backend/llm/prompt_manager.py 保持一致）──

const SUMMARY_SYSTEM_PROMPT = `你是一个专业的会议记录助手。你的任务是对会议转写文本进行结构化摘要。

要求：
1. 提取讨论主题和关键结论
2. 识别 Action Items（含责任人和截止时间，如有提及）
3. 标注重要的技术决策和风险项
4. 使用简洁的条目式输出

严格按照以下格式输出（使用 - 列表，不要使用表格）：

## 主题
- 主题1
- 主题2

## 结论
- 结论1
- 结论2

## Action Items
- 责任人：任务描述（截止时间）
- 责任人：任务描述

## 风险
- 风险1`;

const MERGE_SYSTEM_PROMPT = `你是一个专业的会议记录助手。你的任务是将多个段落摘要合并为一份结构化的全局会议总结。

要求：
1. 合并相同主题，去除重复内容
2. 按讨论顺序组织内容
3. 保留所有 Action Items，不要遗漏

严格按照以下格式输出（使用 - 列表，不要使用表格）：

## 主题
- 主题1
- 主题2

## 结论
- 结论1
- 结论2

## Action Items
- 责任人：任务描述（截止时间）

## 风险
- 风险1`;

// ── 解析 LLM 输出 ──

function parseSummaryResponse(text) {
  const sections = { topics: [], conclusions: [], action_items: [], risks: [] };
  const lines = text.split('\n');
  let current = null;

  for (const line of lines) {
    const stripped = line.trim();
    const lower = stripped.toLowerCase();

    if (stripped.startsWith('#')) {
      if (lower.includes('主题')) current = 'topics';
      else if (lower.includes('结论')) current = 'conclusions';
      else if (lower.includes('action') || lower.includes('待办')) current = 'action_items';
      else if (lower.includes('风险')) current = 'risks';
      else current = null;
      continue;
    }

    if (current && stripped.startsWith('-')) {
      const item = stripped.slice(1).trim();
      if (item) sections[current].push(item);
    }
  }
  return sections;
}

// ── 回看模式：通过 llm:chat 生成摘要 ──

async function generateReviewSummary() {
  const trans = summaryStore.state.reviewTranscriptions;
  if (!trans.length || !window.electronAPI?.llmChat) return;

  const textBlock = trans.map((t) => t.text).join('\n');
  if (!textBlock.trim()) return;

  triggeringSegment.value = true;
  try {
    const result = await window.electronAPI.llmChat({
      messages: [
        { role: 'system', content: SUMMARY_SYSTEM_PROMPT },
        { role: 'user', content: `请对以下会议内容进行摘要：\n\n${textBlock}` },
      ],
      temperature: 0.3,
      max_tokens: 1024,
    });

    if (result.ok && result.content) {
      const parsed = parseSummaryResponse(result.content);
      summaryStore.addSegmentSummary({
        time_range: '完整会议',
        topics: parsed.topics,
        conclusions: parsed.conclusions,
        action_items: parsed.action_items,
        raw_text: result.content,
      });
    } else {
      console.error('[SummaryPanel] LLM error:', result.error);
    }
  } finally {
    triggeringSegment.value = false;
  }
}

async function generateReviewGlobalSummary() {
  const trans = summaryStore.state.reviewTranscriptions;
  const segments = summaryStore.state.segments;
  if (!window.electronAPI?.llmChat) return;

  // 如果没有段落摘要，先生成一个
  if (segments.length === 0 && trans.length > 0) {
    await generateReviewSummary();
  }

  if (summaryStore.state.segments.length === 0) return;

  triggeringGlobal.value = true;
  try {
    const segTexts = summaryStore.state.segments.map((s) => s.rawText).join('\n\n---\n\n');
    const result = await window.electronAPI.llmChat({
      messages: [
        { role: 'system', content: MERGE_SYSTEM_PROMPT },
        {
          role: 'user',
          content: `请将以下段落摘要合并为一份全局会议总结：\n\n${segTexts}`,
        },
      ],
      temperature: 0.3,
      max_tokens: 1500,
    });

    if (result.ok && result.content) {
      const parsed = parseSummaryResponse(result.content);
      summaryStore.updateGlobalSummary({
        raw_text: result.content,
        segments_merged: summaryStore.state.segments.length,
        merge_count: 1,
        action_items: parsed.action_items.map((item) => {
          const colonIdx = item.indexOf('：') !== -1 ? item.indexOf('：') : item.indexOf(':');
          const assignee = colonIdx > 0 ? item.slice(0, colonIdx).trim() : '';
          const desc = colonIdx > 0 ? item.slice(colonIdx + 1).trim() : item;
          return { description: desc, assignee, deadline: '', status: 'open' };
        }),
      });
    } else {
      console.error('[SummaryPanel] LLM merge error:', result.error);
    }
  } finally {
    triggeringGlobal.value = false;
  }
}

// ── 确认覆盖 ──

function confirmOverwrite(type) {
  return window.confirm(
    `已存在${type}，重新生成将覆盖当前内容。是否继续？`,
  );
}

// ── 保存摘要 ──

async function saveSummaries() {
  const meetingId = summaryStore.state.reviewMeetingId;
  if (!meetingId || !window.electronAPI?.saveMeetingSummaries) return;
  saving.value = true;
  try {
    const data = summaryStore.getSummariesForSave();
    const result = await window.electronAPI.saveMeetingSummaries(meetingId, data);
    if (result.ok) {
      summaryStore.markSaved();
    } else {
      console.error('[SummaryPanel] Save failed:', result.error);
      window.alert('保存失败：' + (result.error || '未知错误'));
    }
  } finally {
    saving.value = false;
  }
}

// ── 触发器（自动选择回看/实时模式）──

async function onIntervalChange(event) {
  const val = Number(event.target.value);
  summaryInterval.value = val;
  if (window.electronAPI) {
    await window.electronAPI.sendControl('set_summary_interval', { interval_s: val });
  }
}

async function triggerSegmentSummary() {
  if (triggeringSegment.value) return;
  if (summaryStore.hasSummaryContent.value) {
    if (!confirmOverwrite('段落摘要')) return;
  }
  if (summaryStore.state.reviewMode) {
    await generateReviewSummary();
    return;
  }
  if (!window.electronAPI) return;
  triggeringSegment.value = true;
  try {
    await window.electronAPI.sendControl('trigger_segment_summary');
  } finally {
    triggeringSegment.value = false;
  }
}

async function triggerGlobalSummary() {
  if (triggeringGlobal.value) return;
  if (summaryStore.state.globalSummary) {
    if (!confirmOverwrite('全局总结')) return;
  }
  if (summaryStore.state.reviewMode) {
    await generateReviewGlobalSummary();
    return;
  }
  if (!window.electronAPI) return;
  triggeringGlobal.value = true;
  try {
    await window.electronAPI.sendControl('trigger_global_summary');
  } finally {
    triggeringGlobal.value = false;
  }
}

const activeTab = ref('segment');
const timelineRef = ref(null);

// 自动滚动时间线到底部
watch(
  () => summaryStore.state.segments.length,
  async () => {
    await nextTick();
    if (timelineRef.value) {
      timelineRef.value.scrollTop = timelineRef.value.scrollHeight;
    }
  },
);

// ── 计算属性 ──

/** 最新讨论主题 */
const currentTopics = computed(() => {
  const latest = summaryStore.latestSegment.value;
  return latest ? latest.topics : [];
});

/** 所有段落结论（按时间倒序，最近在前） */
const allConclusions = computed(() => {
  const items = [];
  for (const seg of summaryStore.state.segments) {
    for (const c of seg.conclusions) {
      items.push({ text: c, timeRange: seg.timeRange, segmentId: seg.id });
    }
  }
  return items.reverse();
});

/** 全局 Action Items */
const actionItems = computed(() => summaryStore.actionItems.value);

/** 有内容可展示 */
const hasContent = computed(() => summaryStore.state.segments.length > 0);

/** 全局摘要原文 */
const globalRawText = computed(() => summaryStore.state.globalSummary?.rawText || '');

/** 全局摘要统计 */
const globalStats = computed(() => {
  const gs = summaryStore.state.globalSummary;
  if (!gs) return null;
  return {
    segmentsMerged: gs.segmentsMerged,
    mergeCount: gs.mergeCount,
    lastUpdated: new Date(gs.lastUpdated).toLocaleTimeString('zh-CN', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    }),
  };
});

// ── 方法 ──

function getStatusIcon(status) {
  switch (status) {
    case 'done': return '✅';
    case 'in_progress': return '🔨';
    default: return '⬜';
  }
}

function getStatusClass(status) {
  switch (status) {
    case 'done': return 'status-done';
    case 'in_progress': return 'status-progress';
    default: return 'status-open';
  }
}

function formatUpdatedTime(segment) {
  return new Date(segment.timestamp).toLocaleTimeString('zh-CN', {
    hour: '2-digit', minute: '2-digit',
  });
}
</script>

<template>
  <div class="summary-panel" v-show="summaryStore.state.visible">
    <!-- 标题栏 -->
    <div class="panel-header">
      <div class="header-left">
        <span class="panel-title">📋 会议摘要</span>
        <span v-if="summaryStore.isDirty.value" class="dirty-dot" title="摘要有未保存的修改">●</span>
        <button
          v-if="summaryStore.state.reviewMeetingId && summaryStore.hasSummaryContent.value"
          class="save-btn"
          :class="{ dirty: summaryStore.isDirty.value }"
          :disabled="saving || !summaryStore.isDirty.value"
          @click="saveSummaries"
          :title="summaryStore.isDirty.value ? '保存摘要到会议记录' : '摘要已保存'"
        >
          {{ saving ? '⏳' : '💾' }} {{ summaryStore.isDirty.value ? '保存' : '已保存' }}
        </button>
      </div>
      <div class="header-right">
        <label v-if="!summaryStore.state.reviewMode" class="interval-label" title="自动摘要间隔（60秒 ~ 10分钟）">
          ⏱
          <select class="interval-select" :value="summaryInterval" @change="onIntervalChange">
            <option
              v-for="opt in INTERVAL_OPTIONS"
              :key="opt.value"
              :value="opt.value"
            >
              {{ opt.label }}
            </option>
          </select>
        </label>
        <div class="tab-bar">
          <button
            class="tab-btn"
            :class="{ active: activeTab === 'segment' }"
            @click="activeTab = 'segment'"
          >
            📝 段落摘要
          </button>
          <button
            class="tab-btn"
            :class="{ active: activeTab === 'global' }"
            @click="activeTab = 'global'"
          >
            📊 全局总结
          </button>
          <button
            class="tab-btn"
            :class="{ active: activeTab === 'actions' }"
            @click="activeTab = 'actions'"
          >
            待办
            <span v-if="actionItems.length" class="tab-badge">{{ actionItems.length }}</span>
          </button>
          <button
            class="tab-btn"
            :class="{ active: activeTab === 'timeline' }"
            @click="activeTab = 'timeline'"
          >
            时间线
          </button>
        </div>
      </div>
    </div>

    <!-- 段落摘要视图 -->
    <div v-if="activeTab === 'segment'" class="tab-content">
      <!-- 触发按钮 -->
      <div class="trigger-row">
        <button
          class="trigger-btn"
          @click="triggerSegmentSummary"
          :disabled="triggeringSegment"
        >
          {{ triggeringSegment ? '⏳ 生成中…' : '▶ 生成段落摘要' }}
        </button>
      </div>
      <!-- 当前主题 -->
      <div v-if="currentTopics.length" class="section">
        <div class="section-label">当前讨论主题</div>
        <div class="topic-list">
          <span
            v-for="(topic, i) in currentTopics"
            :key="i"
            class="topic-chip latest"
          >
            {{ topic }}
          </span>
        </div>
      </div>
      <!-- 关键结论 -->
      <div v-if="allConclusions.length" class="section">
        <div class="section-label">关键结论</div>
        <ul class="conclusion-list">
          <li v-for="(item, i) in allConclusions.slice(0, 10)" :key="i" class="conclusion-item">
            <span class="conclusion-time">{{ item.timeRange }}</span>
            <span class="conclusion-text">{{ item.text }}</span>
          </li>
        </ul>
      </div>
      <!-- 空状态 -->
      <div v-if="!hasContent" class="empty-state">
        <div class="empty-icon">📝</div>
        <div class="empty-text">点击上方按钮生成段落摘要</div>
      </div>
    </div>

    <!-- 全局总结视图 -->
    <div v-if="activeTab === 'global'" class="tab-content">
      <!-- 触发按钮 -->
      <div class="trigger-row">
        <button
          class="trigger-btn primary"
          @click="triggerGlobalSummary"
          :disabled="triggeringGlobal"
        >
          {{ triggeringGlobal ? '⏳ 生成中…' : '▶ 生成全局总结' }}
        </button>
      </div>
      <!-- 全局摘要 -->
      <div v-if="globalRawText" class="section">
        <div class="section-label">
          全局总结
          <span v-if="globalStats" class="section-meta">
            · {{ globalStats.segmentsMerged }} 段 · {{ globalStats.lastUpdated }}
          </span>
        </div>
        <div class="global-summary-text">{{ globalRawText }}</div>
      </div>
      <!-- 空状态 -->
      <div v-if="!globalRawText" class="empty-state">
        <div class="empty-icon">📊</div>
        <div class="empty-text">点击上方按钮生成全局会议总结</div>
      </div>
    </div>

    <!-- Action Items 视图 -->
    <div v-if="activeTab === 'actions'" class="tab-content">
      <div v-if="actionItems.length" class="action-list">
        <div
          v-for="(item, i) in actionItems"
          :key="i"
          class="action-item"
          :class="getStatusClass(item.status)"
        >
          <span class="action-status">{{ getStatusIcon(item.status) }}</span>
          <div class="action-body">
            <div class="action-desc">{{ item.description }}</div>
            <div class="action-meta">
              <span v-if="item.assignee" class="action-assignee">
                👤 {{ item.assignee }}
              </span>
              <span v-if="item.deadline" class="action-deadline">
                📅 {{ item.deadline }}
              </span>
            </div>
          </div>
        </div>
      </div>
      <div v-else class="empty-state">
        <div class="empty-icon">✅</div>
        <div class="empty-text">暂无待办事项</div>
      </div>
    </div>

    <!-- 时间线视图 -->
    <div v-if="activeTab === 'timeline'" class="tab-content">
      <div ref="timelineRef" class="timeline-scroll">
        <div v-if="summaryStore.state.segments.length" class="timeline">
          <div
            v-for="seg in summaryStore.state.segments"
            :key="seg.id"
            class="timeline-item"
          >
            <div class="timeline-dot"></div>
            <div class="timeline-content">
              <div class="timeline-header">
                <span class="timeline-time">{{ seg.timeRange }}</span>
                <span class="timeline-updated">{{ formatUpdatedTime(seg) }}</span>
              </div>
              <div v-if="seg.topics.length" class="timeline-topics">
                <span
                  v-for="(t, j) in seg.topics"
                  :key="j"
                  class="topic-chip"
                >
                  {{ t }}
                </span>
              </div>
              <div v-if="seg.conclusions.length" class="timeline-conclusions">
                <div v-for="(c, k) in seg.conclusions" :key="k" class="mini-conclusion">
                  {{ c }}
                </div>
              </div>
              <div v-if="seg.actionItems.length" class="timeline-actions">
                <div v-for="(a, m) in seg.actionItems" :key="m" class="mini-action">
                  ⬜ {{ a }}
                </div>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="empty-state">
          <div class="empty-icon">⏱</div>
          <div class="empty-text">时间线将随会议进行而更新</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.summary-panel {
  display: flex;
  flex-direction: column;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif;
  margin-bottom: 12px;
}

/* ── 标题栏 ── */
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #f5f5f5;
  border-bottom: 1px solid #e0e0e0;
  flex-wrap: wrap;
  gap: 6px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 6px;
}

.panel-title {
  font-size: 13px;
  font-weight: 600;
  color: #333;
}

.dirty-dot {
  color: #f57c00;
  font-size: 10px;
  line-height: 1;
}

.save-btn {
  font-size: 11px;
  padding: 2px 8px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  color: #888;
  cursor: pointer;
  transition: all 0.15s;
}

.save-btn.dirty {
  border-color: #f57c00;
  color: #e65100;
  background: #fff3e0;
}

.save-btn.dirty:hover:not(:disabled) {
  background: #f57c00;
  color: #fff;
}

.save-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

/* ── 触发按钮行 ── */
.trigger-row {
  margin-bottom: 10px;
}

.trigger-btn {
  font-size: 12px;
  padding: 5px 14px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  color: #555;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}

.trigger-btn:hover:not(:disabled) {
  background: #e3f2fd;
  border-color: #90caf9;
  color: #1565c0;
}

.trigger-btn.primary {
  background: #e3f2fd;
  border-color: #90caf9;
  color: #1565c0;
}

.trigger-btn.primary:hover:not(:disabled) {
  background: #1976d2;
  border-color: #1976d2;
  color: #fff;
}

.trigger-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.interval-label {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  color: #666;
  cursor: pointer;
}

.interval-select {
  font-size: 11px;
  padding: 2px 4px;
  border: 1px solid #ccc;
  border-radius: 3px;
  background: #fff;
  color: #555;
  cursor: pointer;
  outline: none;
}

.interval-select:hover {
  border-color: #90caf9;
}

.tab-bar {
  display: flex;
  gap: 2px;
  background: #e0e0e0;
  border-radius: 4px;
  padding: 2px;
}

.tab-btn {
  font-size: 11px;
  padding: 3px 10px;
  border: none;
  border-radius: 3px;
  background: transparent;
  color: #666;
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
}

.tab-btn.active {
  background: #fff;
  color: #333;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
}

.tab-btn:hover:not(.active) {
  color: #333;
}

.tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: #1976d2;
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  margin-left: 4px;
}

/* ── Tab 内容 ── */
.tab-content {
  padding: 10px 12px;
  max-height: 350px;
  overflow-y: auto;
}

.tab-content::-webkit-scrollbar {
  width: 4px;
}

.tab-content::-webkit-scrollbar-thumb {
  background: #ddd;
  border-radius: 2px;
}

/* ── Section ── */
.section {
  margin-bottom: 12px;
}

.section:last-child {
  margin-bottom: 0;
}

.section-label {
  font-size: 11px;
  font-weight: 600;
  color: #888;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
}

.section-meta {
  font-weight: 400;
  color: #aaa;
  text-transform: none;
  letter-spacing: 0;
}

/* ── 主题标签 ── */
.topic-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.topic-chip {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  background: #e3f2fd;
  color: #1565c0;
  white-space: nowrap;
}

.topic-chip.latest {
  background: #1976d2;
  color: #fff;
  font-weight: 500;
}

/* ── 全局摘要 ── */
.global-summary-text {
  font-size: 13px;
  line-height: 1.6;
  color: #444;
  white-space: pre-wrap;
  background: #fafafa;
  border: 1px solid #eee;
  border-radius: 6px;
  padding: 8px 10px;
  max-height: 150px;
  overflow-y: auto;
}

/* ── 结论列表 ── */
.conclusion-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.conclusion-item {
  display: flex;
  gap: 8px;
  font-size: 13px;
  line-height: 1.4;
  padding: 3px 0;
}

.conclusion-time {
  font-size: 11px;
  color: #aaa;
  flex-shrink: 0;
  min-width: 80px;
}

.conclusion-text {
  color: #333;
}

/* ── Action Items ── */
.action-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.action-item {
  display: flex;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  background: #fafafa;
  border: 1px solid #eee;
  transition: background 0.15s;
}

.action-item:hover {
  background: #f5f5f5;
}

.action-item.status-done {
  opacity: 0.6;
}

.action-item.status-progress {
  border-left: 3px solid #ff9800;
}

.action-item.status-open {
  border-left: 3px solid #1976d2;
}

.action-status {
  font-size: 14px;
  flex-shrink: 0;
  line-height: 1.4;
}

.action-body {
  flex: 1;
  min-width: 0;
}

.action-desc {
  font-size: 13px;
  color: #333;
  line-height: 1.4;
}

.action-meta {
  display: flex;
  gap: 10px;
  margin-top: 3px;
}

.action-assignee,
.action-deadline {
  font-size: 11px;
  color: #888;
}

/* ── 时间线 ── */
.timeline-scroll {
  max-height: 320px;
  overflow-y: auto;
}

.timeline-scroll::-webkit-scrollbar {
  width: 4px;
}

.timeline-scroll::-webkit-scrollbar-thumb {
  background: #ddd;
  border-radius: 2px;
}

.timeline {
  position: relative;
  padding-left: 20px;
}

.timeline::before {
  content: '';
  position: absolute;
  left: 6px;
  top: 0;
  bottom: 0;
  width: 2px;
  background: #e0e0e0;
}

.timeline-item {
  position: relative;
  padding-bottom: 14px;
}

.timeline-item:last-child {
  padding-bottom: 0;
}

.timeline-dot {
  position: absolute;
  left: -17px;
  top: 4px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #1976d2;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px #e0e0e0;
}

.timeline-item:last-child .timeline-dot {
  background: #4caf50;
  box-shadow: 0 0 0 1px #4caf50, 0 0 4px rgba(76, 175, 80, 0.3);
}

.timeline-content {
  padding: 0;
}

.timeline-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.timeline-time {
  font-size: 12px;
  font-weight: 600;
  color: #555;
}

.timeline-updated {
  font-size: 10px;
  color: #bbb;
}

.timeline-topics {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  margin-bottom: 4px;
}

.timeline-conclusions {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 4px;
}

.mini-conclusion {
  font-size: 12px;
  color: #555;
  line-height: 1.4;
  padding-left: 10px;
  border-left: 2px solid #e0e0e0;
}

.timeline-actions {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.mini-action {
  font-size: 11px;
  color: #888;
  line-height: 1.4;
}

/* ── 空状态 ── */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 24px 0;
  color: #bbb;
}

.empty-icon {
  font-size: 28px;
  margin-bottom: 8px;
}

.empty-text {
  font-size: 13px;
  font-style: italic;
}
</style>
