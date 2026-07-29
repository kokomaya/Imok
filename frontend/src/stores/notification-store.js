/**
 * 全局通知（错误提示）存储。
 *
 * 单一职责：集中管理顶部错误提示条的消息与自动消失。
 * 供组件与纯 JS service（expression-service、llm-client 等）统一调用，
 * 使任意位置产生的错误都能反馈到 UI，而不只是打印到控制台。
 */

import { reactive } from 'vue';

const state = reactive({
  errorMessage: '',
});

let timer = null;

/**
 * 显示一条错误提示。
 * 若与当前正在显示的消息相同，仅刷新自动消失计时，避免重复触发时的闪烁。
 * @param {string} msg 错误信息
 * @param {number} [duration=8000] 自动消失毫秒
 */
function notifyError(msg, duration = 8000) {
  if (!msg) return;
  state.errorMessage = String(msg);
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    state.errorMessage = '';
    timer = null;
  }, duration);
}

/** 立即清除当前提示。 */
function dismiss() {
  state.errorMessage = '';
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
}

export const notificationStore = { state, notifyError, dismiss };
