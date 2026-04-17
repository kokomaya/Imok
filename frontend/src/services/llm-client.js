/**
 * LLM 直连客户端 — Renderer 侧翻译服务。
 *
 * 单一职责：调用远程 LLM API（OpenAI 兼容 SSE Streaming），
 * 将 ASR 转写文本翻译并以流式方式更新 subtitle store。
 *
 * 不负责 IPC 通信或 UI 渲染。
 *
 * 配置来源：由 main 进程通过 IPC 传递，或在初始化时设置。
 */

import { subtitleStore } from '@/stores/subtitle-store.js';

// ---------------------------------------------------------------
// 配置
// ---------------------------------------------------------------

/** @type {LLMConfig | null} */
let config = null;

/**
 * @typedef {Object} LLMConfig
 * @property {string} baseUrl - API base URL
 * @property {string} model - 模型名称
 * @property {string} apiKey - API 密钥
 * @property {Record<string, string>} [headers] - 额外请求头
 * @property {number} [timeout=30000] - 超时毫秒
 * @property {boolean} [sslVerify=true] - 是否验证 SSL（仅 Node 环境有效）
 */

/** 翻译 prompt 模板 */
const TRANSLATION_PROMPT = `你是实时会议翻译系统，请将以下内容翻译为{targetLang}：
要求：
- 保留技术术语
- 简洁自然
- 不添加解释
- 仅输出翻译结果

输入：
{text}`;

/**
 * 初始化 LLM 客户端配置。
 * @param {LLMConfig} cfg
 */
function init(cfg) {
  config = { ...cfg };
}

/**
 * 判断目标翻译语言。
 * 中文 → 英文，英文 → 中文，其他 → 英文。
 * @param {string} sourceLang
 * @returns {string}
 */
function getTargetLang(sourceLang) {
  const lang = (sourceLang || '').toLowerCase();
  if (lang.startsWith('zh') || lang === 'chinese') return '英文';
  return '中文';
}

/**
 * 对指定字幕条目执行流式翻译。
 * @param {{ id: number, original: string, language: string }} entry
 */
async function translateEntry(entry) {
  if (!config) {
    console.warn('[llm-client] Not initialized, skipping translation');
    subtitleStore.markTranslationError(entry.id);
    return;
  }

  if (!entry.original || entry.original.trim().length === 0) {
    subtitleStore.updateTranslation(entry.id, '', true);
    return;
  }

  const targetLang = getTargetLang(entry.language);
  const prompt = TRANSLATION_PROMPT
    .replace('{targetLang}', targetLang)
    .replace('{text}', entry.original);

  const body = {
    model: config.model,
    messages: [{ role: 'user', content: prompt }],
    stream: true,
    temperature: 0.3,
    max_tokens: 512,
  };

  const headers = {
    'Content-Type': 'application/json',
    ...(config.apiKey ? { Authorization: `Bearer ${config.apiKey}` } : {}),
    ...(config.headers || {}),
  };

  const url = `${config.baseUrl.replace(/\/+$/, '')}/chat/completions`;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), config.timeout || 30000);

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    await readSSEStream(response, entry.id);
  } catch (err) {
    if (err.name === 'AbortError') {
      console.warn('[llm-client] Translation timed out for entry', entry.id);
    } else {
      console.error('[llm-client] Translation failed:', err.message);
    }
    subtitleStore.markTranslationError(entry.id);
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * 读取 SSE 流并逐步更新翻译文本。
 * @param {Response} response
 * @param {number} entryId
 */
async function readSSEStream(response, entryId) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;

        const payload = trimmed.slice(6);
        if (payload === '[DONE]') {
          subtitleStore.updateTranslation(entryId, '', true);
          return;
        }

        try {
          const parsed = JSON.parse(payload);
          const delta = parsed.choices?.[0]?.delta?.content;
          if (delta) {
            subtitleStore.updateTranslation(entryId, delta, false);
          }

          // 检查 finish_reason
          const finishReason = parsed.choices?.[0]?.finish_reason;
          if (finishReason === 'stop') {
            subtitleStore.updateTranslation(entryId, '', true);
            return;
          }
        } catch (_) {
          // 解析错误，跳过这一行
        }
      }
    }

    // 流结束但没有收到 [DONE]
    subtitleStore.updateTranslation(entryId, '', true);
  } finally {
    reader.releaseLock();
  }
}

/**
 * 是否已配置。
 * @returns {boolean}
 */
function isConfigured() {
  return config !== null && !!config.baseUrl && !!config.model;
}

/**
 * 重置配置。
 */
function reset() {
  config = null;
}

export const llmClient = {
  init,
  translateEntry,
  isConfigured,
  reset,
};
