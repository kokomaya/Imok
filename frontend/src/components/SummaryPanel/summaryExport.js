/**
 * 会议摘要导出 / 复制辅助。
 *
 * 单一职责：把 summaryStore 中的结构化摘要组装成可读的 Markdown 文本，
 * 以及提供剪贴板复制能力。不持有 UI 状态。
 */

import { summaryStore } from '@/stores/summary-store.js';

/** 状态图标（与面板保持一致）。 */
function statusIcon(status) {
  if (status === 'done') return '✅';
  if (status === 'in_progress') return '🔨';
  return '⬜';
}

/**
 * 组装单个段落摘要为 Markdown。优先使用结构化字段，缺失时回退到 rawText。
 * @param {import('@/stores/summary-store.js').SegmentSummary} seg
 * @returns {string}
 */
export function buildSegmentMarkdown(seg) {
  const hasStructured =
    (seg.topics && seg.topics.length) ||
    (seg.conclusions && seg.conclusions.length) ||
    (seg.actionItems && seg.actionItems.length);

  if (!hasStructured) {
    return (seg.rawText || '').trim();
  }

  const lines = [];
  if (seg.timeRange) lines.push(`### ${seg.timeRange}`);
  if (seg.topics?.length) {
    lines.push('**主题**');
    for (const t of seg.topics) lines.push(`- ${t}`);
  }
  if (seg.conclusions?.length) {
    lines.push('**结论**');
    for (const c of seg.conclusions) lines.push(`- ${c}`);
  }
  if (seg.actionItems?.length) {
    lines.push('**Action Items**');
    for (const a of seg.actionItems) lines.push(`- ${a}`);
  }
  return lines.join('\n');
}

/**
 * 组装全局 Action Items 为 Markdown。
 * @param {import('@/stores/summary-store.js').ActionItem[]} items
 * @returns {string}
 */
export function buildActionItemsMarkdown(items) {
  if (!items || !items.length) return '';
  const lines = ['## 待办事项'];
  for (const it of items) {
    const parts = [`${statusIcon(it.status)} ${it.description || ''}`.trim()];
    if (it.assignee) parts.push(`👤 ${it.assignee}`);
    if (it.deadline) parts.push(`📅 ${it.deadline}`);
    lines.push(`- ${parts.join('  ·  ')}`);
  }
  return lines.join('\n');
}

/**
 * 组装完整会议摘要为 Markdown（全局总结 + 段落摘要 + 待办）。
 * @param {{ includeSegments?: boolean, includeGlobal?: boolean, includeActions?: boolean }} [opts]
 * @returns {string}
 */
export function buildFullSummaryMarkdown(opts = {}) {
  const {
    includeSegments = true,
    includeGlobal = true,
    includeActions = true,
  } = opts;
  const state = summaryStore.state;
  const blocks = [];

  const title = state.reviewMode && state.reviewMeetingId
    ? `# 会议摘要（${state.reviewMeetingId}）`
    : '# 会议摘要';
  blocks.push(title);

  if (includeGlobal && state.globalSummary?.rawText?.trim()) {
    blocks.push('## 全局总结\n\n' + state.globalSummary.rawText.trim());
  }

  if (includeActions) {
    const ai = buildActionItemsMarkdown(state.globalSummary?.actionItems || []);
    if (ai) blocks.push(ai);
  }

  if (includeSegments && state.segments.length) {
    const segBlocks = state.segments
      .map(buildSegmentMarkdown)
      .filter((s) => s.trim());
    if (segBlocks.length) {
      blocks.push('## 段落摘要\n\n' + segBlocks.join('\n\n---\n\n'));
    }
  }

  return blocks.join('\n\n').trim();
}

/**
 * 复制文本到剪贴板。优先使用异步 Clipboard API，失败时回退到 execCommand。
 * @param {string} text
 * @returns {Promise<boolean>} 是否成功
 */
export async function copyToClipboard(text) {
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) {
    /* fall through to legacy path */
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch (_) {
    return false;
  }
}
