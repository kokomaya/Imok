/**
 * 会议摘要数据存储。
 *
 * 单一职责：管理摘要数据的响应式状态。
 * 由 IPC bridge 写入（接收 Python 子进程推送的摘要），
 * 由 SummaryPanel 组件消费（Task 3.5）。
 */

import { reactive, computed } from 'vue';

/**
 * @typedef {Object} SegmentSummary
 * @property {number} id - 唯一标识
 * @property {string} timeRange - 时间范围
 * @property {string[]} topics - 讨论主题
 * @property {string[]} conclusions - 关键结论
 * @property {string[]} actionItems - 行动项（文本）
 * @property {string} rawText - LLM 原始输出
 * @property {number} timestamp - 接收时间 (epoch ms)
 */

/**
 * @typedef {Object} ActionItem
 * @property {string} description - 事项描述
 * @property {string} assignee - 责任人
 * @property {string} deadline - 截止时间
 * @property {'open' | 'in_progress' | 'done'} status
 */

/**
 * @typedef {Object} GlobalSummaryState
 * @property {string} rawText - LLM 原始输出
 * @property {number} segmentsMerged - 已合并段落数
 * @property {number} mergeCount - 合并次数
 * @property {ActionItem[]} actionItems - 结构化 Action Items
 * @property {number} lastUpdated - 最后更新时间 (epoch ms)
 */

const MAX_SEGMENTS = 100;

let nextSegmentId = 1;

/** 上次保存时的摘要快照哈希，用于脏检测 */
let _savedHash = '';

const state = reactive({
  /** @type {SegmentSummary[]} */
  segments: [],

  /** @type {GlobalSummaryState | null} */
  globalSummary: null,

  /** 总结模块是否正在处理 */
  processing: false,

  /** 流式生成中的文本（逐 chunk 拼接，生成完成后清空） */
  generatingText: '',

  /** 面板是否可见 */
  visible: false,

  /** 是否处于历史回看模式 */
  reviewMode: false,

  /** 回看模式下的原始转写文本列表 @type {{ text: string, timestamp: number }[]} */
  reviewTranscriptions: [],

  /** 当前回看的会议 ID */
  reviewMeetingId: '',

  /** 当前实时会议 ID（由 App.vue 根据 python:status 设置） */
  liveMeetingId: '',

  /** 实时模式下的转写文本列表（由 App.vue 喂入，供前端降级生成摘要） @type {{ text: string, timestamp: number, start: number|null, end: number|null }[]} */
  liveTranscriptions: [],

  /** 时段摘要结果（独立于段落/全局摘要） @type {{ id: number, label: string, timeRange: string, rawText: string, timestamp: number }[]} */
  timeRangeSummaries: [],
});

// ── 脏检测 ──

/**
 * 生成当前摘要内容的简单哈希字符串。
 * 仅用于比较是否有变化，不要求加密安全。
 */
function _contentHash() {
  const segPart = state.segments.map((s) =>
    `${s.rawText}|${s.topics.join(',')}|${s.conclusions.join(',')}|${s.actionItems.join(',')}`
  ).join('||');
  const globalPart = state.globalSummary?.rawText || '';
  const aiPart = (state.globalSummary?.actionItems || []).map(
    (a) => `${a.description}|${a.assignee}|${a.deadline}|${a.status}`
  ).join('||');
  return `${segPart}::${globalPart}::${aiPart}`;
}

/**
 * 摘要内容是否在上次保存后发生了变化。
 */
const isDirty = computed(() => {
  return _contentHash() !== _savedHash;
});

/**
 * 标记当前状态为已保存。
 */
function markSaved() {
  _savedHash = _contentHash();
}

/**
 * 是否已有段落摘要或全局总结。
 */
const hasSummaryContent = computed(() => {
  return state.segments.length > 0 || state.globalSummary !== null;
});

/** 当前可保存的会议 ID（回看模式用 reviewMeetingId，实时模式用 liveMeetingId） */
const activeMeetingId = computed(() => {
  return state.reviewMeetingId || state.liveMeetingId;
});

/**
 * 添加一条段落摘要（从 Python IPC 推送接收）。
 * @param {Object} data - IPC segment_summary data payload
 * @returns {SegmentSummary}
 */
function addSegmentSummary(data) {
  const entry = {
    id: nextSegmentId++,
    timeRange: data.time_range || '',
    topics: data.topics || [],
    conclusions: data.conclusions || [],
    actionItems: data.action_items || [],
    rawText: data.raw_text || '',
    timestamp: Date.now(),
  };

  state.segments.push(entry);

  if (state.segments.length > MAX_SEGMENTS) {
    state.segments.splice(0, state.segments.length - MAX_SEGMENTS);
  }

  return entry;
}

/**
 * 更新全局会议总结（从 Python IPC 推送接收）。
 * @param {Object} data - IPC global_summary data payload
 */
function updateGlobalSummary(data) {
  state.globalSummary = {
    rawText: data.raw_text || '',
    segmentsMerged: data.segments_merged || 0,
    mergeCount: data.merge_count || 0,
    actionItems: (data.action_items || []).map((item) => ({
      description: item.description || '',
      assignee: item.assignee || '',
      deadline: item.deadline || '',
      status: item.status || 'open',
    })),
    lastUpdated: Date.now(),
  };
}

/**
 * 清空所有摘要数据。
 */
function clearAll() {
  state.segments.splice(0, state.segments.length);
  state.globalSummary = null;
  state.processing = false;
  state.generatingText = '';
  state.reviewMode = false;
  state.reviewMeetingId = '';
  state.liveMeetingId = '';
  state.reviewTranscriptions.splice(0, state.reviewTranscriptions.length);
  state.liveTranscriptions.splice(0, state.liveTranscriptions.length);
  state.timeRangeSummaries.splice(0, state.timeRangeSummaries.length);
  _savedHash = '';
}

/**
 * 开始流式生成（清空暂存文本，设置 processing）。
 */
function startGenerating() {
  state.processing = true;
  state.generatingText = '';
}

/**
 * 追加一段流式 delta 文本。
 * @param {string} delta
 */
function appendGeneratingChunk(delta) {
  state.generatingText += delta;
}

/**
 * 结束流式生成。
 */
function stopGenerating() {
  state.processing = false;
  state.generatingText = '';
}

/**
 * 设置历史回看数据。
 * @param {{ text: string, timestamp: number, start?: number|null, end?: number|null }[]} transcriptions
 * @param {string} [meetingId]
 */
function setReviewData(transcriptions, meetingId = '') {
  state.reviewMode = true;
  state.reviewMeetingId = meetingId;
  const normalized = (transcriptions || []).map((t) => ({
    text: t.text,
    timestamp: t.timestamp || 0,
    start: typeof t.start === 'number' ? t.start : null,
    end: typeof t.end === 'number' ? t.end : null,
  }));
  state.reviewTranscriptions.splice(0, state.reviewTranscriptions.length, ...normalized);
}

/**
 * 退出回看模式。
 */
function clearReviewData() {
  state.reviewMode = false;
  state.reviewTranscriptions.splice(0, state.reviewTranscriptions.length);
}

/**
 * 设置当前实时会议 ID。
 * @param {string} meetingId
 */
function setLiveMeetingId(meetingId) {
  state.liveMeetingId = meetingId;
}

/**
 * 追加一条实时转写文本。
 * @param {{ text: string, timestamp: number, start?: number|null, end?: number|null }} entry
 */
function addLiveTranscription(entry) {
  state.liveTranscriptions.push({
    text: entry.text,
    timestamp: entry.timestamp,
    start: typeof entry.start === 'number' ? entry.start : null,
    end: typeof entry.end === 'number' ? entry.end : null,
  });
}

let nextTimeRangeId = 1;

/** 当前模式下的转写列表（回看用 review，实时用 live）。 */
const activeTranscriptions = computed(() => {
  return state.reviewMode ? state.reviewTranscriptions : state.liveTranscriptions;
});

/**
 * 归一化转写为会议相对秒（0 基）。优先用后端提供的 start/end；
 * 缺失时用 epoch 时间戳相对首条推算。
 * @returns {{ items: { text: string, start: number, end: number }[], duration: number }}
 */
function normalizedTranscripts() {
  const raw = activeTranscriptions.value;
  if (!raw.length) return { items: [], duration: 0 };

  const hasRelative = raw.some((t) => typeof t.start === 'number');
  let baseTs = Infinity;
  for (const t of raw) {
    if (typeof t.timestamp === 'number' && t.timestamp < baseTs) baseTs = t.timestamp;
  }
  if (!Number.isFinite(baseTs)) baseTs = 0;

  const items = raw.map((t) => {
    let start;
    let end;
    if (typeof t.start === 'number') {
      start = t.start;
      end = typeof t.end === 'number' ? t.end : t.start;
    } else {
      start = Math.max(0, (t.timestamp || baseTs) - baseTs);
      end = start;
    }
    return { text: t.text || '', start, end };
  });

  let duration = 0;
  for (const it of items) duration = Math.max(duration, it.end, it.start);
  // 无相对时间且全部同一时刻时，给一个最小跨度避免滑块塌缩
  if (!hasRelative && duration <= 0) duration = items.length;
  return { items, duration };
}

/**
 * 取指定会议相对时间区间 [startSec, endSec] 内的转写文本块。
 * @param {number} startSec
 * @param {number} endSec
 * @returns {string}
 */
function transcriptTextInRange(startSec, endSec) {
  const { items } = normalizedTranscripts();
  const lo = Math.min(startSec, endSec);
  const hi = Math.max(startSec, endSec);
  return items
    .filter((it) => it.end >= lo && it.start <= hi)
    .map((it) => it.text)
    .filter((s) => s && s.trim())
    .join('\n');
}

/**
 * 取最接近指定会议相对秒的转写文本（滑块拖动时预览用）。
 * 优先命中覆盖该秒的转写；否则取中心时间最近的一条。
 * @param {number} sec
 * @returns {string}
 */
function transcriptAtSec(sec) {
  const { items } = normalizedTranscripts();
  if (!items.length) return '';
  const hit = items.find((it) => sec >= it.start && sec <= it.end);
  if (hit) return hit.text;
  let best = items[0];
  let bestDist = Infinity;
  for (const it of items) {
    const center = (it.start + it.end) / 2;
    const d = Math.abs(center - sec);
    if (d < bestDist) { bestDist = d; best = it; }
  }
  return best.text;
}

/**
 * 添加一条时段摘要结果（独立于段落/全局摘要）。
 * @param {{ label: string, timeRange: string, rawText: string }} data
 */
function addTimeRangeSummary(data) {
  state.timeRangeSummaries.unshift({
    id: nextTimeRangeId++,
    label: data.label || '',
    timeRange: data.timeRange || '',
    rawText: data.rawText || '',
    timestamp: Date.now(),
  });
}

/** 删除一条时段摘要。 */
function removeTimeRangeSummary(id) {
  const idx = state.timeRangeSummaries.findIndex((s) => s.id === id);
  if (idx !== -1) state.timeRangeSummaries.splice(idx, 1);
}

/** 所有主题（从所有段落摘要合并去重） */
const allTopics = computed(() => {
  const set = new Set();
  for (const seg of state.segments) {
    for (const t of seg.topics) set.add(t);
  }
  return [...set];
});

/** 最新的段落摘要 */
const latestSegment = computed(() => {
  return state.segments.length > 0 ? state.segments[state.segments.length - 1] : null;
});

/** 全局 Action Items */
const actionItems = computed(() => {
  return state.globalSummary?.actionItems || [];
});

/**
 * 切换面板可见性。
 */
function toggleVisible() {
  state.visible = !state.visible;
}

// ── 编辑 API（回看模式下手动微调摘要）──

/**
 * 编辑段落摘要的指定字段。
 * @param {number} segmentId
 * @param {'topics' | 'conclusions' | 'actionItems' | 'rawText'} field
 * @param {*} value
 */
function editSegmentField(segmentId, field, value) {
  const seg = state.segments.find(s => s.id === segmentId);
  if (!seg) return;
  seg[field] = value;
}

/**
 * 编辑全局摘要原文。
 * @param {string} newText
 */
function editGlobalRawText(newText) {
  if (!state.globalSummary) return;
  state.globalSummary.rawText = newText;
}

/**
 * 编辑 Action Item 的指定字段。
 * @param {number} index
 * @param {'description' | 'assignee' | 'deadline' | 'status'} field
 * @param {string} value
 */
function editActionItem(index, field, value) {
  const item = state.globalSummary?.actionItems?.[index];
  if (!item) return;
  item[field] = value;
}

/**
 * 获取可序列化的摘要数据（用于持久化）。
 * @returns {{ segments: Object[], global_summary: Object|null, action_items: Object[] }}
 */
function getSummariesForSave() {
  const segments = state.segments.map((s) => ({
    summary_type: 'segment',
    raw_text: s.rawText,
    time_range: s.timeRange,
    topics: [...s.topics],
    conclusions: [...s.conclusions],
    action_items: [...s.actionItems],
    timestamp: s.timestamp / 1000,
  }));

  let globalSummary = null;
  const actionItems = [];

  if (state.globalSummary) {
    globalSummary = {
      summary_type: 'global',
      raw_text: state.globalSummary.rawText,
      segments_merged: state.globalSummary.segmentsMerged,
      merge_count: state.globalSummary.mergeCount,
      timestamp: state.globalSummary.lastUpdated / 1000,
    };
    for (const ai of state.globalSummary.actionItems) {
      actionItems.push({
        description: ai.description,
        assignee: ai.assignee,
        deadline: ai.deadline || '',
        status: ai.status || 'open',
        source: '',
      });
    }
  }

  return JSON.parse(JSON.stringify({ segments, global_summary: globalSummary, action_items: actionItems }));
}

export const summaryStore = {
  state,
  allTopics,
  latestSegment,
  actionItems,
  isDirty,
  hasSummaryContent,
  activeMeetingId,
  addSegmentSummary,
  updateGlobalSummary,
  clearAll,
  toggleVisible,
  editSegmentField,
  editGlobalRawText,
  editActionItem,
  setReviewData,
  clearReviewData,
  setLiveMeetingId,
  addLiveTranscription,
  activeTranscriptions,
  normalizedTranscripts,
  transcriptTextInRange,
  transcriptAtSec,
  addTimeRangeSummary,
  removeTimeRangeSummary,
  markSaved,
  getSummariesForSave,
  startGenerating,
  appendGeneratingChunk,
  stopGenerating,
};
