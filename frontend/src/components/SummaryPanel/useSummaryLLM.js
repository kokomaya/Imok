/**
 * LLM 摘要生成逻辑 — 从 SummaryPanel 提取。
 *
 * 包含 LLM prompts、响应解析、4 种摘要生成函数。
 * 所有函数都是纯逻辑，不持有 UI 状态（ref/reactive）。
 */

import { summaryStore } from '@/stores/summary-store.js';
import { notificationStore } from '@/stores/notification-store.js';
import { formatLLMError } from '@/services/llm-error.js';
import { summaryTemplateStore } from '@/stores/summary-template-store.js';

// ── 解析 LLM 输出 ──

export function parseSummaryResponse(text) {
  const sections = { topics: [], conclusions: [], action_items: [], risks: [] };
  const lines = text.split('\n');
  const titleMap = summaryTemplateStore.sectionTitleMap();
  const keyToField = {
    topics: 'topics',
    conclusions: 'conclusions',
    actions: 'action_items',
    risks: 'risks',
  };
  let current = null;

  for (const line of lines) {
    const stripped = line.trim();
    const lower = stripped.toLowerCase();

    if (stripped.startsWith('#')) {
      const heading = stripped.replace(/^#+\s*/, '').trim().toLowerCase();
      // 优先匹配自定义模板章节标题
      let matched = null;
      for (const [title, key] of Object.entries(titleMap)) {
        if (title && heading.includes(title)) { matched = keyToField[key]; break; }
      }
      if (matched) current = matched;
      else if (lower.includes('主题')) current = 'topics';
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

  const { summarySystem } = summaryTemplateStore.frontendPrompts();
  const result = await _chatWithStreaming({
    messages: [
      { role: 'system', content: summarySystem },
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
  const { mergeSystem } = summaryTemplateStore.frontendPrompts();
  const result = await _chatWithStreaming({
    messages: [
      { role: 'system', content: mergeSystem },
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

  const { summarySystem } = summaryTemplateStore.frontendPrompts();
  const result = await _chatWithStreaming({
    messages: [
      { role: 'system', content: summarySystem },
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

// ── 时段摘要：对指定时间区间的转写文本生成独立摘要 ──

// 时段摘要专用提示词：聚焦所选时间段「在讨论什么」，
// 侧重结合上下文的逐句翻译/整理与概要，并自行纠正语音识别偏差。
const TIME_RANGE_SYSTEM_PROMPT = `你是一名专业的会议记录与翻译助手。用户会给你一段来自「语音转文字」的会议转写，且仅为所选时间段的内容。
由于是语音识别结果，文本可能存在错字、断句错误、同音词误识别、缺词漏词等问题；请结合上下文自行推断、补全并纠正，使内容通顺可读。

只针对这一时间段的内容进行处理，不要臆造该片段之外的信息。核心目标是让用户清楚「这段时间到底在讨论什么」。

请按以下结构输出（Markdown）：

## 讨论主题
用一两句话概括这段时间的核心主题。

## 逐句翻译整理
在结合上下文纠正识别错误的前提下，按说话顺序逐句给出中文翻译/整理（原文已是中文则做通顺化整理），一句一行，尽量忠于原意。

## 内容概要
用要点形式（以 - 开头）总结这段时间讨论的具体内容与要点。

说明：纠错以「结合上下文最合理」为准；确实无法确定的词可保留原词并用（?）标注。`;

/**
 * 对会议相对时间区间 [startSec, endSec] 内的转写文本生成一份独立摘要。
 * 结果存入 summaryStore.timeRangeSummaries，不影响段落/全局摘要。
 * 实时与回看模式都通过 electron llm:chat 代理，不依赖 Python 后端。
 * @param {{ startSec: number, endSec: number, label: string, timeRange: string }} params
 * @returns {Promise<boolean>} 是否成功生成
 */
export async function generateTimeRangeSummary({ startSec, endSec, label, timeRange }) {
  const textBlock = summaryStore.transcriptTextInRange(startSec, endSec);
  if (!textBlock.trim()) {
    notificationStore.notifyError('所选时间段内没有可用的转写内容');
    return false;
  }

  const result = await _chatWithStreaming({
    messages: [
      { role: 'system', content: TIME_RANGE_SYSTEM_PROMPT },
      { role: 'user', content: `以下是所选时间段（${timeRange}）的语音转写内容，请按要求处理：\n\n${textBlock}` },
    ],
    temperature: 0.3,
    max_tokens: 2048,
  }, '时段摘要生成失败');

  if (result.ok && result.content) {
    summaryStore.addTimeRangeSummary({
      label: label || timeRange,
      timeRange,
      rawText: result.content,
    });
    return true;
  }
  return false;
}

export async function generateLiveGlobalSummary() {
  if (summaryStore.state.segments.length === 0 && summaryStore.state.liveTranscriptions.length > 0) {
    await generateLiveSegmentSummary();
  }
  if (summaryStore.state.segments.length === 0) return;

  const segTexts = summaryStore.state.segments.map((s) => s.rawText).join('\n\n---\n\n');
  const { mergeSystem } = summaryTemplateStore.frontendPrompts();
  const result = await _chatWithStreaming({
    messages: [
      { role: 'system', content: mergeSystem },
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
