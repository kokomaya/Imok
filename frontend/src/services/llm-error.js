/**
 * LLM 错误格式化工具。
 *
 * 单一职责：把 LLM 返回的原始错误（可能是多层嵌套的 JSON 字符串）
 * 提取为面向用户的可读文本，供所有调用 LLM 的位置统一使用。
 */

/**
 * 把 LLM 错误字符串提取为可读信息。
 * 兼容形如：
 *   HTTP 500: {"error":{"message":"Error forwarding request: {\n \"response\": \"...\"}\n","type":"..."}}
 * @param {string} error 原始错误字符串
 * @param {string} [prefix='LLM 请求失败'] 面向用户的前缀
 * @returns {string} 面向用户的可读错误信息
 */
export function formatLLMError(error, prefix = 'LLM 请求失败') {
  if (!error) return `${prefix}：未知错误`;
  const raw = String(error);

  const httpMatch = raw.match(/HTTP\s+(\d+)/i);
  const httpCode = httpMatch ? httpMatch[1] : '';

  let detail = raw;
  const braceIdx = raw.indexOf('{');
  if (braceIdx !== -1) {
    try {
      const body = JSON.parse(raw.slice(braceIdx));
      let inner = body?.error?.message ?? body?.message ?? '';
      // inner 可能再嵌套一层 JSON：{ "response": "..." }
      const innerBrace = typeof inner === 'string' ? inner.indexOf('{') : -1;
      if (innerBrace !== -1) {
        try {
          const nested = JSON.parse(inner.slice(innerBrace));
          if (nested?.response) inner = nested.response;
        } catch (_) { /* 保留原 inner */ }
      }
      if (inner) detail = String(inner);
    } catch (_) { /* 落到兜底：使用原始字符串 */ }
  }

  detail = detail.replace(/\s+/g, ' ').trim();
  return httpCode
    ? `${prefix} (HTTP ${httpCode})：${detail}`
    : `${prefix}：${detail}`;
}
