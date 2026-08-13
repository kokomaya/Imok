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
import { workspaceStore } from '@/stores/workspace-store.js';
import {
  generateReviewSummary,
  generateReviewGlobalSummary,
  generateLiveSegmentSummary,
  generateLiveGlobalSummary,
  generateTimeRangeSummary,
} from './useSummaryLLM.js';
import InlineEdit from '@/components/common/InlineEdit.vue';
import EditableField from '@/components/common/EditableField.vue';
import SummaryPreviewDialog from './SummaryPreviewDialog.vue';
import {
  buildFullSummaryMarkdown,
  buildSegmentMarkdown,
  copyToClipboard,
} from './summaryExport.js';
import { notificationStore } from '@/stores/notification-store.js';

const triggeringSegment = ref(false);
const triggeringGlobal = ref(false);
const summaryInterval = ref(600);
// 默认手动摘要：定时摘要需用户主动开启
const autoSummary = ref(false);

// ── 预览 / 复制 ──
const previewVisible = ref(false);
const previewMarkdown = ref('');
const copiedKey = ref('');
let copiedTimer = null;

function openPreview() {
  previewMarkdown.value = buildFullSummaryMarkdown();
  previewVisible.value = true;
}

async function copyMarkdown(text, key) {
  if (!text || !text.trim()) return;
  const ok = await copyToClipboard(text);
  if (ok) {
    copiedKey.value = key;
    clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => { copiedKey.value = ''; }, 1600);
  } else {
    notificationStore.notifyError('复制失败，请手动选择文本复制');
  }
}

function copyGlobal() {
  copyMarkdown(summaryStore.state.globalSummary?.rawText || '', 'global');
}

function copyAllSegments() {
  const text = summaryStore.state.segments
    .map(buildSegmentMarkdown)
    .filter((s) => s.trim())
    .join('\n\n---\n\n');
  copyMarkdown(text, 'segments');
}

// ── 时段摘要（按时间条选取区间，独立于段落/全局摘要）──
const triggeringRange = ref(false);
const rangeStartRaw = ref(0);
const rangeEndRaw = ref(0);
const rangeTouched = ref(false);

/** 当前模式下转写的会议相对时长（秒，向上取整）。 */
const meetingDuration = computed(() =>
  Math.max(0, Math.ceil(summaryStore.normalizedTranscripts().duration)),
);

const hasTranscripts = computed(() => meetingDuration.value > 0);

// 时长变化时：未手动调整则跟随整场；已手动则夹取到合法范围
watch(meetingDuration, (d) => {
  if (!rangeTouched.value) {
    rangeStartRaw.value = 0;
    rangeEndRaw.value = d;
  } else {
    rangeStartRaw.value = Math.min(rangeStartRaw.value, d);
    rangeEndRaw.value = Math.min(rangeEndRaw.value, d);
  }
}, { immediate: true });

const rangeStart = computed(() => Math.min(rangeStartRaw.value, rangeEndRaw.value));
const rangeEnd = computed(() => Math.max(rangeStartRaw.value, rangeEndRaw.value));

function formatSec(s) {
  const total = Math.max(0, Math.round(s));
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}

const rangeLabel = computed(() => `${formatSec(rangeStart.value)} - ${formatSec(rangeEnd.value)}`);

function onRangeStartInput(e) {
  rangeTouched.value = true;
  activePreset.value = 'custom';
  draggingHandle.value = 'start';
  rangeStartRaw.value = Math.min(Number(e.target.value), rangeEndRaw.value);
}
function onRangeEndInput(e) {
  rangeTouched.value = true;
  activePreset.value = 'custom';
  draggingHandle.value = 'end';
  rangeEndRaw.value = Math.max(Number(e.target.value), rangeStartRaw.value);
}
// 松开滑块后收起预览气泡
function endRangeDrag() {
  draggingHandle.value = null;
}

// 仅在拖动时激活：当前拖动端所在秒位置的字幕预览
const draggingHandle = ref(null); // 'start' | 'end' | null
const rangePreviewSec = computed(() =>
  draggingHandle.value === 'end' ? rangeEnd.value : rangeStart.value,
);
const rangePreviewText = computed(() =>
  draggingHandle.value ? summaryStore.transcriptAtSec(rangePreviewSec.value) : '',
);
const rangePreviewTime = computed(() =>
  draggingHandle.value ? formatSec(rangePreviewSec.value) : '',
);
const rangePreviewPercent = computed(() => {
  if (!draggingHandle.value || !meetingDuration.value) return 0;
  return Math.min(100, Math.max(0, (rangePreviewSec.value / meetingDuration.value) * 100));
});

const RANGE_PRESETS = [
  { key: 'all', label: '全局', seconds: 0 },
  { key: 'r2', label: '最近2分钟', seconds: 120 },
  { key: 'r5', label: '最近5分钟', seconds: 300 },
  { key: 'r10', label: '最近10分钟', seconds: 600 },
];
const activePreset = ref('all');

function applyPreset(preset) {
  rangeTouched.value = true;
  activePreset.value = preset.key;
  const d = meetingDuration.value;
  if (preset.key === 'all') {
    rangeStartRaw.value = 0;
    rangeEndRaw.value = d;
  } else {
    rangeStartRaw.value = Math.max(0, d - preset.seconds);
    rangeEndRaw.value = d;
  }
}

async function generateRange() {
  if (triggeringRange.value || !hasTranscripts.value) return;
  const isFull = rangeStart.value <= 0 && rangeEnd.value >= meetingDuration.value;
  const label = isFull ? `全局（${rangeLabel.value}）` : rangeLabel.value;
  triggeringRange.value = true;
  try {
    await generateTimeRangeSummary({
      startSec: rangeStart.value,
      endSec: rangeEnd.value,
      label,
      timeRange: rangeLabel.value,
    });
  } finally {
    triggeringRange.value = false;
  }
}

function copyRangeSummary(item) {
  copyMarkdown(item.rawText, `range-${item.id}`);
}
function removeRangeSummary(id) {
  summaryStore.removeTimeRangeSummary(id);
}


const INTERVAL_OPTIONS = [
  { label: '20分钟', value: 1200 },
  { label: '15分钟', value: 900 },
  { label: '10分钟', value: 600 },
  { label: '5分钟', value: 300 },
  { label: '3分钟', value: 180 },
  { label: '2分钟', value: 120 },
  { label: '1分钟', value: 60 },
];

// ── 确认覆盖 ──

function confirmOverwrite(type) {
  return window.confirm(
    `已存在${type}，重新生成将覆盖当前内容。是否继续？`,
  );
}

// ── 触发器（自动选择回看/实时模式）──

async function onIntervalChange(event) {
  const val = Number(event.target.value);
  summaryInterval.value = val;
  if (window.electronAPI) {
    await window.electronAPI.sendControl('set_summary_interval', { interval_s: val });
  }
}

async function onToggleAutoSummary() {
  autoSummary.value = !autoSummary.value;
  if (window.electronAPI) {
    await window.electronAPI.sendControl('set_auto_summary', { enabled: autoSummary.value });
  }
}

// 会议启动时把当前自动摘要设置同步到后端（后端每场会议会新建协调器）
watch(
  () => summaryStore.state.liveMeetingId,
  async (meetingId) => {
    if (!meetingId || !window.electronAPI) return;
    await window.electronAPI.sendControl('set_summary_interval', { interval_s: summaryInterval.value });
    await window.electronAPI.sendControl('set_auto_summary', { enabled: autoSummary.value });
  },
);

async function triggerSegmentSummary() {
  if (triggeringSegment.value) return;
  if (summaryStore.hasSummaryContent.value) {
    if (!confirmOverwrite('段落摘要')) return;
  }
  if (summaryStore.state.reviewMode) {
    triggeringSegment.value = true;
    try {
      await generateReviewSummary();
    } finally {
      triggeringSegment.value = false;
    }
    return;
  }
  if (!window.electronAPI) return;

  // 优先走后端 coordinator；Python 未运行时降级为前端直接调用 LLM
  triggeringSegment.value = true;
  try {
    const resp = await window.electronAPI.sendControl('trigger_segment_summary');
    if (resp?.ok) return;
    // 后端不可用，用前端 LLM 降级
    await generateLiveSegmentSummary();
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
    triggeringGlobal.value = true;
    try {
      await generateReviewGlobalSummary();
    } finally {
      triggeringGlobal.value = false;
    }
    return;
  }
  if (!window.electronAPI) return;

  // 全局摘要本质是合并已有段落 — 优先后端，不可用时前端处理
  triggeringGlobal.value = true;
  try {
    const resp = await window.electronAPI.sendControl('trigger_global_summary');
    if (resp?.ok) return;
    await generateLiveGlobalSummary();
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

// ── 编辑能力（仅回看模式） ──

/** 回看模式下允许编辑 */
const editable = computed(() => summaryStore.state.reviewMode);

const ACTION_STATUS_OPTIONS = [
  { value: 'open', label: '⬜ 待处理' },
  { value: 'in_progress', label: '🔨 进行中' },
  { value: 'done', label: '✅ 已完成' },
];

function onEditSegmentTopics(segId, items) {
  summaryStore.editSegmentField(segId, 'topics', items);
}

function onEditSegmentConclusions(segId, items) {
  summaryStore.editSegmentField(segId, 'conclusions', items);
}

function onEditSegmentActionItems(segId, items) {
  summaryStore.editSegmentField(segId, 'actionItems', items);
}

function onEditGlobalRawText(text) {
  summaryStore.editGlobalRawText(text);
}

function onEditConclusion(segmentId, oldText, newText) {
  const seg = summaryStore.state.segments.find(s => s.id === segmentId);
  if (!seg) return;
  const idx = seg.conclusions.indexOf(oldText);
  if (idx === -1) return;
  const copy = [...seg.conclusions];
  copy[idx] = newText;
  summaryStore.editSegmentField(segmentId, 'conclusions', copy);
}

function onEditActionItem(index, field, value) {
  summaryStore.editActionItem(index, field, value);
}
</script>

<template>
  <div class="summary-panel" v-show="summaryStore.state.visible">
    <!-- 标题栏 -->
    <div class="panel-header">
      <div class="header-left">
        <span class="panel-title">📋 会议摘要</span>
        <button
          class="preview-btn"
          @click="openPreview"
          :disabled="!hasContent && !globalRawText"
          title="弹出预览完整摘要（可一键复制）"
        >🔍 预览</button>
      </div>
      <div class="header-right">
        <button
          v-if="!summaryStore.state.reviewMode"
          class="auto-toggle"
          :class="{ on: autoSummary }"
          @click="onToggleAutoSummary"
          :title="autoSummary ? '自动定时摘要：开（点击关闭，改为手动触发）' : '自动定时摘要：关（点击开启定时）'"
        >
          <span class="auto-dot"></span>
          {{ autoSummary ? '自动摘要' : '手动摘要' }}
        </button>
        <label v-if="!summaryStore.state.reviewMode && autoSummary" class="interval-label" title="自动摘要间隔（60秒 ~ 20分钟）">
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
            :class="{ active: activeTab === 'timerange' }"
            @click="activeTab = 'timerange'"
          >
            🕘 时段摘要
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
      <div v-if="currentTopics.length || editable" class="section">
        <div class="section-label">当前讨论主题</div>
        <EditableField
          v-if="editable && summaryStore.latestSegment.value"
          :items="currentTopics"
          :disabled="!editable"
          addLabel="+ 添加主题"
          @update:items="onEditSegmentTopics(summaryStore.latestSegment.value.id, $event)"
        />
        <div v-else class="topic-list">
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
        <div class="section-label">
          关键结论
          <button
            class="copy-btn"
            :class="{ done: copiedKey === 'segments' }"
            @click="copyAllSegments"
            title="复制全部段落摘要"
          >{{ copiedKey === 'segments' ? '✓ 已复制' : '📋 复制' }}</button>
        </div>
        <ul class="conclusion-list">
          <li v-for="(item, i) in allConclusions.slice(0, 10)" :key="i" class="conclusion-item">
            <span class="conclusion-time">{{ item.timeRange }}</span>
            <InlineEdit
              v-if="editable"
              :modelValue="item.text"
              tag="span"
              class="conclusion-text"
              @update:modelValue="onEditConclusion(item.segmentId, item.text, $event)"
            />
            <span v-else class="conclusion-text">{{ item.text }}</span>
          </li>
        </ul>
      </div>
      <!-- 流式生成中 -->
      <div v-if="triggeringSegment && summaryStore.state.generatingText" class="section generating-section">
        <div class="section-label">生成中…</div>
        <div class="generating-text">{{ summaryStore.state.generatingText }}<span class="cursor-blink">▍</span></div>
      </div>
      <!-- 空状态 -->
      <div v-if="!hasContent && !triggeringSegment" class="empty-state">
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
      <!-- 流式生成中 -->
      <div v-if="triggeringGlobal && summaryStore.state.generatingText" class="section generating-section">
        <div class="section-label">生成中…</div>
        <div class="generating-text">{{ summaryStore.state.generatingText }}<span class="cursor-blink">▍</span></div>
      </div>
      <!-- 全局摘要 -->
      <div v-if="globalRawText && !triggeringGlobal" class="section">
        <div class="section-label">
          全局总结
          <span v-if="globalStats" class="section-meta">
            · {{ globalStats.segmentsMerged }} 段 · {{ globalStats.lastUpdated }}
          </span>
          <button
            class="copy-btn"
            :class="{ done: copiedKey === 'global' }"
            @click="copyGlobal"
            title="复制全局总结"
          >{{ copiedKey === 'global' ? '✓ 已复制' : '📋 复制' }}</button>
        </div>
        <InlineEdit
          v-if="editable"
          :modelValue="globalRawText"
          multiline
          tag="div"
          class="global-summary-text"
          @update:modelValue="onEditGlobalRawText"
        />
        <div v-else class="global-summary-text">{{ globalRawText }}</div>
      </div>
      <!-- 空状态 -->
      <div v-if="!globalRawText && !triggeringGlobal" class="empty-state">
        <div class="empty-icon">📊</div>
        <div class="empty-text">点击上方按钮生成全局会议总结</div>
      </div>
    </div>

    <!-- 时段摘要视图 -->
    <div v-if="activeTab === 'timerange'" class="tab-content">
      <div class="range-controls">
        <div class="range-presets">
          <button
            v-for="p in RANGE_PRESETS"
            :key="p.key"
            class="range-preset-btn"
            :class="{ active: activePreset === p.key }"
            :disabled="!hasTranscripts"
            @click="applyPreset(p)"
          >{{ p.label }}</button>
        </div>

        <div class="range-slider" :class="{ disabled: !hasTranscripts }">
          <div class="range-track">
            <div
              class="range-fill"
              :style="{
                left: meetingDuration ? (rangeStart / meetingDuration * 100) + '%' : '0%',
                right: meetingDuration ? (100 - rangeEnd / meetingDuration * 100) + '%' : '0%',
              }"
            ></div>
            <div
              v-if="draggingHandle"
              class="range-preview-popup"
              :style="{ left: rangePreviewPercent + '%' }"
            >
              <div class="range-preview-time">{{ rangePreviewTime }}</div>
              <div class="range-preview-text">{{ rangePreviewText || '（此处暂无字幕）' }}</div>
            </div>
            <input
              class="range-input range-input-start"
              type="range"
              min="0"
              :max="meetingDuration || 1"
              step="1"
              :value="rangeStart"
              :disabled="!hasTranscripts"
              @input="onRangeStartInput"
              @change="endRangeDrag"
              @pointerup="endRangeDrag"
              @blur="endRangeDrag"
            />
            <input
              class="range-input range-input-end"
              type="range"
              min="0"
              :max="meetingDuration || 1"
              step="1"
              :value="rangeEnd"
              :disabled="!hasTranscripts"
              @input="onRangeEndInput"
              @change="endRangeDrag"
              @pointerup="endRangeDrag"
              @blur="endRangeDrag"
            />
          </div>
          <div class="range-scale">
            <span>00:00</span>
            <span class="range-selected">{{ rangeLabel }}</span>
            <span>{{ formatSec(meetingDuration) }}</span>
          </div>
        </div>

        <div class="trigger-row">
          <button
            class="trigger-btn primary"
            :disabled="triggeringRange || !hasTranscripts"
            @click="generateRange"
          >
            {{ triggeringRange ? '⏳ 生成中…' : '▶ 生成时段摘要' }}
          </button>
        </div>
        <div v-if="!hasTranscripts" class="range-hint">
          暂无转写内容，开始会议或载入历史会议后即可按时间段生成摘要。
        </div>
      </div>

      <!-- 流式生成中 -->
      <div v-if="triggeringRange && summaryStore.state.generatingText" class="section generating-section">
        <div class="section-label">生成中…</div>
        <div class="generating-text">{{ summaryStore.state.generatingText }}<span class="cursor-blink">▍</span></div>
      </div>

      <!-- 结果列表 -->
      <div v-if="summaryStore.state.timeRangeSummaries.length" class="range-results">
        <div
          v-for="item in summaryStore.state.timeRangeSummaries"
          :key="item.id"
          class="range-result-card"
        >
          <div class="range-result-head">
            <span class="range-result-label">🕘 {{ item.label }}</span>
            <div class="range-result-actions">
              <button
                class="copy-btn"
                :class="{ done: copiedKey === `range-${item.id}` }"
                @click="copyRangeSummary(item)"
                title="复制此时段摘要"
              >{{ copiedKey === `range-${item.id}` ? '✓ 已复制' : '📋 复制' }}</button>
              <button class="range-del-btn" @click="removeRangeSummary(item.id)" title="删除此条">🗑</button>
            </div>
          </div>
          <div class="range-result-text">{{ item.rawText }}</div>
        </div>
      </div>
      <div v-else-if="!triggeringRange" class="empty-state">
        <div class="empty-icon">🕘</div>
        <div class="empty-text">拖动时间条或选择预设，生成某一时间段的独立摘要</div>
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
          <template v-if="editable">
            <select
              class="action-status-select"
              :value="item.status"
              @change="onEditActionItem(i, 'status', $event.target.value)"
            >
              <option
                v-for="opt in ACTION_STATUS_OPTIONS"
                :key="opt.value"
                :value="opt.value"
              >{{ opt.label }}</option>
            </select>
          </template>
          <span v-else class="action-status">{{ getStatusIcon(item.status) }}</span>
          <div class="action-body">
            <InlineEdit
              v-if="editable"
              :modelValue="item.description"
              tag="div"
              class="action-desc"
              @update:modelValue="onEditActionItem(i, 'description', $event)"
            />
            <div v-else class="action-desc">{{ item.description }}</div>
            <div class="action-meta">
              <span v-if="item.assignee || editable" class="action-assignee">
                👤
                <InlineEdit
                  v-if="editable"
                  :modelValue="item.assignee"
                  tag="span"
                  placeholder="责任人"
                  @update:modelValue="onEditActionItem(i, 'assignee', $event)"
                />
                <template v-else>{{ item.assignee }}</template>
              </span>
              <span v-if="item.deadline || editable" class="action-deadline">
                📅
                <InlineEdit
                  v-if="editable"
                  :modelValue="item.deadline"
                  tag="span"
                  placeholder="截止时间"
                  @update:modelValue="onEditActionItem(i, 'deadline', $event)"
                />
                <template v-else>{{ item.deadline }}</template>
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
              <div v-if="seg.topics.length || editable" class="timeline-topics">
                <EditableField
                  v-if="editable"
                  :items="seg.topics"
                  addLabel="+ 主题"
                  @update:items="onEditSegmentTopics(seg.id, $event)"
                />
                <template v-else>
                  <span
                    v-for="(t, j) in seg.topics"
                    :key="j"
                    class="topic-chip"
                  >
                    {{ t }}
                  </span>
                </template>
              </div>
              <div v-if="seg.conclusions.length || editable" class="timeline-conclusions">
                <EditableField
                  v-if="editable"
                  :items="seg.conclusions"
                  addLabel="+ 结论"
                  @update:items="onEditSegmentConclusions(seg.id, $event)"
                />
                <template v-else>
                  <div v-for="(c, k) in seg.conclusions" :key="k" class="mini-conclusion">
                    {{ c }}
                  </div>
                </template>
              </div>
              <div v-if="seg.actionItems.length || editable" class="timeline-actions">
                <EditableField
                  v-if="editable"
                  :items="seg.actionItems"
                  addLabel="+ 行动项"
                  @update:items="onEditSegmentActionItems(seg.id, $event)"
                />
                <template v-else>
                  <div v-for="(a, m) in seg.actionItems" :key="m" class="mini-action">
                    ⬜ {{ a }}
                  </div>
                </template>
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

  <SummaryPreviewDialog
    :visible="previewVisible"
    :markdown="previewMarkdown"
    @close="previewVisible = false"
  />
</template>

<style scoped src="./SummaryPanel.scoped.css"></style>
