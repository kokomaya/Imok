/**
 * LLM 摘要生成逻辑 — 从 SummaryPanel 提取。
 *
 * 包含 LLM prompts、响应解析、4 种摘要生成函数。
 * 所有函数都是纯逻辑，不持有 UI 状态（ref/reactive）。
 */

import { summaryStore } from '@/stores/summary-store.js';
import { notificationStore } from '@/stores/notification-store.js';
import { formatLLMError } from '@/services/llm-error.js';
import { SUMMARY_SYSTEM_PROMPT, MERGE_SYSTEM_PROMPT } from '@/prompts/index.js';

// ── 解析 LLM 输出 ──

export function parseSummaryResponse(text) {
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

/**
 * 解析 action item 文本为结构化对象。
 */
function parseActionItem(item) {
  const colonIdx = item.indexOf('：') !== -1 ? item.indexOf('：') : item.indexOf(':');
  const assignee = colonIdx > 0 ? item.slice(0, colonIdx).trim() : '';
  const desc = colonIdx > 0 ? item.slice(colonIdx + 1).trim() : item;
  return { description: desc, assignee, deadline: '', status: 'open' };
}

// ── 流式 / 非流式自适应调用 ──

/**
 * 调用 LLM，优先使用流式接口（实时显示生成文本），不支持时回退到非流式。
 * 错误上报收敛在此单一出口：失败时直接将可读错误推送到全局错误栏，
 * 调用方无需重复处理。
 * @param {{ messages: Array, temperature?: number, max_tokens?: number }} params
 * @param {string} errorLabel 失败时面向用户的错误前缀
 * @returns {Promise<{ ok: boolean, content?: string, error?: string }>}
 */
async function _chatWithStreaming(params, errorLabel = 'LLM 请求失败') {
  let result;
  if (window.electronAPI?.llmChatStream) {
    summaryStore.startGenerating();
    try {
      result = await window.electronAPI.llmChatStream(params, {
        onChunk: (delta) => summaryStore.appendGeneratingChunk(delta),
      });
    } finally {
      summaryStore.stopGenerating();
    }
  } else if (window.electronAPI?.llmChat) {
    // 回退：非流式
    result = await window.electronAPI.llmChat(params);
  } else {
    result = { ok: false, error: 'No LLM API available' };
  }

  if (!result.ok) {
    console.error(`[SummaryLLM] ${errorLabel}:`, result.error);
    notificationStore.notifyError(formatLLMError(result.error, errorLabel));
  }
  return result;
}

// ── 回看模式：通过 llm:chat 生成摘要 ──

export async function generateReviewSummary() {
  const trans = summaryStore.state.reviewTranscriptions;
  if (!trans.length) return;

  const textBlock = trans.map((t) => t.text).join('\n');
  if (!textBlock.trim()) return;

  const result = await _chatWithStreaming({
    messages: [
      { role: 'system', content: SUMMARY_SYSTEM_PROMPT },
      { role: 'user', content: `请对以下会议内容进行摘要：\n\n${textBlock}` },
    ],
    temperature: 0.3,
    max_tokens: 1024,
  }, '摘要生成失败');

  if (result.ok && result.content) {
    const parsed = parseSummaryResponse(result.content);
    summaryStore.addSegmentSummary({
      time_range: '完整会议',
      topics: parsed.topics,
      conclusions: parsed.conclusions,
      action_items: parsed.action_items,
      raw_text: result.content,
    });
  }
}

export async function generateReviewGlobalSummary() {
  const trans = summaryStore.state.reviewTranscriptions;
  const segments = summaryStore.state.segments;

  if (segments.length === 0 && trans.length > 0) {
    await generateReviewSummary();
  }

  if (summaryStore.state.segments.length === 0) return;

  const segTexts = summaryStore.state.segments.map((s) => s.rawText).join('\n\n---\n\n');
  const result = await _chatWithStreaming({
    messages: [
      { role: 'system', content: MERGE_SYSTEM_PROMPT },
      {
        role: 'user',
        content: `请将以下段落摘要合并为一份全局会议总结：\n\n${segTexts}`,
      },
    ],
    temperature: 0.3,
    max_tokens: 1500,
  }, '总结生成失败');

  if (result.ok && result.content) {
    const parsed = parseSummaryResponse(result.content);
    summaryStore.updateGlobalSummary({
      raw_text: result.content,
      segments_merged: summaryStore.state.segments.length,
      merge_count: 1,
      action_items: parsed.action_items.map(parseActionItem),
    });
  }
}

// ── 前端降级：实时模式直接调用 LLM ──

export async function generateLiveSegmentSummary() {
  const trans = summaryStore.state.liveTranscriptions;
  if (!trans.length) return;

  const textBlock = trans.map((t) => t.text).join('\n');
  if (!textBlock.trim()) return;

  const result = await _chatWithStreaming({
    messages: [
      { role: 'system', content: SUMMARY_SYSTEM_PROMPT },
      { role: 'user', content: `请对以下会议内容进行摘要：\n\n${textBlock}` },
    ],
    temperature: 0.3,
    max_tokens: 1024,
  }, '摘要生成失败');

  if (result.ok && result.content) {
    const parsed = parseSummaryResponse(result.content);
    summaryStore.addSegmentSummary({
      time_range: '完整会议',
      topics: parsed.topics,
      conclusions: parsed.conclusions,
      action_items: parsed.action_items,
      raw_text: result.content,
    });
  }
}

export async function generateLiveGlobalSummary() {
  if (summaryStore.state.segments.length === 0 && summaryStore.state.liveTranscriptions.length > 0) {
    await generateLiveSegmentSummary();
  }
  if (summaryStore.state.segments.length === 0) return;

  const segTexts = summaryStore.state.segments.map((s) => s.rawText).join('\n\n---\n\n');
  const result = await _chatWithStreaming({
    messages: [
      { role: 'system', content: MERGE_SYSTEM_PROMPT },
      { role: 'user', content: `请将以下段落摘要合并为一份全局会议总结：\n\n${segTexts}` },
    ],
    temperature: 0.3,
    max_tokens: 1500,
  }, '总结生成失败');

  if (result.ok && result.content) {
    const parsed = parseSummaryResponse(result.content);
    summaryStore.updateGlobalSummary({
      raw_text: result.content,
      segments_merged: summaryStore.state.segments.length,
      merge_count: 1,
      action_items: parsed.action_items.map(parseActionItem),
    });
  }
}
