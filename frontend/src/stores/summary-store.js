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

const state = reactive({
  /** @type {SegmentSummary[]} */
  segments: [],

  /** @type {GlobalSummaryState | null} */
  globalSummary: null,

  /** 总结模块是否正在处理 */
  processing: false,

  /** 面板是否可见 */
  visible: true,
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

export const summaryStore = {
  state,
  allTopics,
  latestSegment,
  actionItems,
  addSegmentSummary,
  updateGlobalSummary,
  clearAll,
  toggleVisible,
};
