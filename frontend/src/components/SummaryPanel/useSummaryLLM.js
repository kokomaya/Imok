/**
 * LLM 摘要生成逻辑 — 从 SummaryPanel 提取。
 *
 * 包含 LLM prompts、响应解析、4 种摘要生成函数。
 * 所有函数都是纯逻辑，不持有 UI 状态（ref/reactive）。
 */

import { summaryStore } from '@/stores/summary-store.js';

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

// ── 回看模式：通过 llm:chat 生成摘要 ──

export async function generateReviewSummary() {
  const trans = summaryStore.state.reviewTranscriptions;
  if (!trans.length || !window.electronAPI?.llmChat) return;

  const textBlock = trans.map((t) => t.text).join('\n');
  if (!textBlock.trim()) return;

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
}

export async function generateReviewGlobalSummary() {
  const trans = summaryStore.state.reviewTranscriptions;
  const segments = summaryStore.state.segments;
  if (!window.electronAPI?.llmChat) return;

  if (segments.length === 0 && trans.length > 0) {
    await generateReviewSummary();
  }

  if (summaryStore.state.segments.length === 0) return;

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
      action_items: parsed.action_items.map(parseActionItem),
    });
  } else {
    console.error('[SummaryPanel] LLM merge error:', result.error);
  }
}

// ── 前端降级：实时模式直接调用 LLM ──

export async function generateLiveSegmentSummary() {
  const trans = summaryStore.state.liveTranscriptions;
  if (!trans.length || !window.electronAPI?.llmChat) return;

  const textBlock = trans.map((t) => t.text).join('\n');
  if (!textBlock.trim()) return;

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
    console.error('[SummaryPanel] Fallback LLM error:', result.error);
  }
}

export async function generateLiveGlobalSummary() {
  if (summaryStore.state.segments.length === 0 && summaryStore.state.liveTranscriptions.length > 0) {
    await generateLiveSegmentSummary();
  }
  if (summaryStore.state.segments.length === 0) return;
  if (!window.electronAPI?.llmChat) return;

  const segTexts = summaryStore.state.segments.map((s) => s.rawText).join('\n\n---\n\n');
  const result = await window.electronAPI.llmChat({
    messages: [
      { role: 'system', content: MERGE_SYSTEM_PROMPT },
      { role: 'user', content: `请将以下段落摘要合并为一份全局会议总结：\n\n${segTexts}` },
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
      action_items: parsed.action_items.map(parseActionItem),
    });
  } else {
    console.error('[SummaryPanel] Fallback LLM merge error:', result.error);
  }
}
