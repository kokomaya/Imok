/**
 * 闭麦表达辅助服务 — Renderer 侧。
 *
 * 单一职责：调用远程 LLM API（OpenAI 兼容 SSE Streaming），
 * 将中文输入转换为适合会议场景的英文表达。
 *
 * 不负责 UI 渲染或 IPC 通信。
 */

import { muteAssistStore } from '@/stores/mute-assist-store.js';
import { sceneStore } from '@/stores/scene-store.js';
import { expressionSettingsStore } from '@/stores/expression-settings-store.js';
import { buildExpressionPrompt, EXPRESSION_SYSTEM_PROMPT } from '@/prompts/index.js';

// ---------------------------------------------------------------
// 配置
// ---------------------------------------------------------------

/** @type {import('@/services/llm-client.js').LLMConfig | null} */
let config = null;

/** 当前进行中的 AbortController */
let activeController = null;

/**
 * 初始化表达服务配置。
 * @param {import('@/services/llm-client.js').LLMConfig} cfg
 */
function init(cfg) {
  config = { ...cfg };
}

/**
 * 执行表达转换（键盘输入模式）。
 * @param {string} inputText - 中文输入文本
 */
async function express(inputText) {
  if (!config) {
    console.warn('[expression-service] Not initialized');
    return;
  }

  const text = inputText.trim();
  if (!text) return;

  // 取消之前正在进行的请求标记
  abort();

  const id = muteAssistStore.startExpression(text);

  try {
    // 动态构建 prompt：注入场景描述 + 候选条数
    const scene = sceneStore.activeScene.value;
    const candidateCount = expressionSettingsStore.state.candidateCount;
    const systemPrompt = scene
      ? buildExpressionPrompt(scene.description, candidateCount)
      : EXPRESSION_SYSTEM_PROMPT;

    const result = await window.electronAPI.llmChat({
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: text },
      ],
      temperature: candidateCount > 1 ? 0.7 : 0.3,
      max_tokens: candidateCount > 1 ? 1024 : 512,
    });

    if (!result.ok) {
      throw new Error(result.error);
    }

    if (result.content) {
      muteAssistStore.appendOutput(id, result.content);
    }
    muteAssistStore.finishExpression(id);
  } catch (err) {
    console.error('[expression-service] Request failed:', err.message);
    muteAssistStore.markError(id);
  }
}

/**
 * 读取 SSE 流并更新 store。
 * @param {Response} response
 * @param {number} id
 */
async function readSSEStream(response, id) {
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
          muteAssistStore.finishExpression(id);
          return;
        }

        try {
          const parsed = JSON.parse(payload);
          const delta = parsed.choices?.[0]?.delta?.content;
          if (delta) {
            muteAssistStore.appendOutput(id, delta);
          }

          const finishReason = parsed.choices?.[0]?.finish_reason;
          if (finishReason === 'stop') {
            muteAssistStore.finishExpression(id);
            return;
          }
        } catch (_) {
          // 解析错误，跳过
        }
      }
    }

    // 流结束
    muteAssistStore.finishExpression(id);
  } finally {
    reader.releaseLock();
  }
}

/**
 * 读取非流式 JSON 响应并更新 store。
 * @param {Response} response
 * @param {number} id
 */
async function readJSONResponse(response, id) {
  const data = await response.json();
  const content = data.choices?.[0]?.message?.content || '';
  if (content) {
    muteAssistStore.appendOutput(id, content);
  }
  muteAssistStore.finishExpression(id);
}

/**
 * 取消当前正在进行的请求。
 */
function abort() {
  if (activeController) {
    activeController.abort();
    activeController = null;
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
 * 重置。
 */
function reset() {
  abort();
  config = null;
}

export const expressionService = {
  init,
  express,
  abort,
  isConfigured,
  reset,
};
