/**
 * 文本操作服务 — 对选中文本执行翻译 / 解释（LLM）。
 *
 * 单一职责：封装右键菜单等场景对选中文本的 LLM 处理，
 * 统一复用 llm:chat 代理、错误格式化与全局错误通知。
 * 不持有 UI 状态，返回结果由调用方决定如何呈现。
 */

import { TRANSLATION_PROMPT, EXPLAIN_PROMPT } from '@/prompts/index.js';
import { formatLLMError } from '@/services/llm-error.js';
import { notificationStore } from '@/stores/notification-store.js';

/** 是否包含中日韩字符（用于判断翻译目标语言）。 */
function hasCJK(text) {
  return /[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]/.test(text);
}

/**
 * 统一的一次性 LLM 调用（非流式），失败时上报全局错误栏。
 * @param {string} systemOrUserContent 完整的用户消息内容
 * @param {string} errorLabel 失败前缀
 * @returns {Promise<{ ok: boolean, content?: string }>}
 */
async function _chat(content, errorLabel) {
  if (!window.electronAPI?.llmChat) {
    notificationStore.notifyError(`${errorLabel}：LLM 不可用`);
    return { ok: false };
  }
  const result = await window.electronAPI.llmChat({
    messages: [{ role: 'user', content }],
    temperature: 0.3,
    max_tokens: 512,
  });
  if (!result.ok) {
    notificationStore.notifyError(formatLLMError(result.error, errorLabel));
    return { ok: false };
  }
  return { ok: true, content: (result.content || '').trim() };
}

/**
 * 翻译选中文本（中↔英自动判向）。
 * @param {string} text
 * @returns {Promise<{ ok: boolean, content?: string }>}
 */
export function translateText(text) {
  const targetLang = hasCJK(text) ? '英文' : '中文';
  const content = TRANSLATION_PROMPT.replace('{targetLang}', targetLang).replace('{text}', text);
  return _chat(content, '翻译失败');
}

/**
 * 解释选中文本。
 * @param {string} text
 * @returns {Promise<{ ok: boolean, content?: string }>}
 */
export function explainText(text) {
  const content = EXPLAIN_PROMPT.replace('{text}', text);
  return _chat(content, '解释失败');
}
