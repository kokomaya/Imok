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
});

// ── 脏检测 ──

/**
 * 生成当前摘要内容的简单哈希字符串。
 * 仅用于比较是否有变化，不要求加密安全。
 */
function _contentHash() {
  const segPart = state.segments.map((s) => s.rawText).join('|');
  const globalPart = state.globalSummary?.rawText || '';
  return `${segPart}::${globalPart}`;
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
  state.reviewMode = false;
  state.reviewMeetingId = '';
  state.liveMeetingId = '';
  state.reviewTranscriptions.splice(0, state.reviewTranscriptions.length);
  _savedHash = '';
}

/**
 * 设置历史回看数据。
 * @param {{ text: string, timestamp: number }[]} transcriptions
 * @param {string} [meetingId]
 */
function setReviewData(transcriptions, meetingId = '') {
  state.reviewMode = true;
  state.reviewMeetingId = meetingId;
  state.reviewTranscriptions.splice(0, state.reviewTranscriptions.length, ...transcriptions);
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

/**
 * 获取可序列化的摘要数据（用于持久化）。
 * @returns {{ segments: Object[], global_summary: Object|null, action_items: Object[] }}
 */
function getSummariesForSave() {
  const segments = state.segments.map((s) => ({
    summary_type: 'segment',
    raw_text: s.rawText,
    time_range: s.timeRange,
    topics: s.topics,
    conclusions: s.conclusions,
    action_items: s.actionItems,
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

  return { segments, global_summary: globalSummary, action_items: actionItems };
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
  setReviewData,
  clearReviewData,
  setLiveMeetingId,
  markSaved,
  getSummariesForSave,
};
