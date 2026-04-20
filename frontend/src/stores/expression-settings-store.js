/**
 * 表达助手设置存储。
 *
 * 单一职责：管理闭麦表达的偏好设置（候选条数等）。
 * 独立于工作区，全局持久化到 config/expression_settings.json。
 */

import { reactive } from 'vue';

const DEFAULTS = {
  candidateCount: 1,  // 候选表达条数，1~5
};

const state = reactive({
  candidateCount: DEFAULTS.candidateCount,
});

async function load() {
  if (!window.electronAPI?.getExpressionSettings) return;
  try {
    const result = await window.electronAPI.getExpressionSettings();
    if (result.ok && result.settings) {
      if (typeof result.settings.candidateCount === 'number') {
        state.candidateCount = Math.max(1, Math.min(5, result.settings.candidateCount));
      }
    }
  } catch (err) {
    console.error('[expression-settings-store] Failed to load:', err);
  }
}

async function _persist() {
  if (!window.electronAPI?.saveExpressionSettings) return;
  try {
    await window.electronAPI.saveExpressionSettings({
      candidateCount: state.candidateCount,
    });
  } catch (err) {
    console.error('[expression-settings-store] Failed to save:', err);
  }
}

/**
 * 设置候选条数。
 * @param {number} n - 1~5
 */
function setCandidateCount(n) {
  state.candidateCount = Math.max(1, Math.min(5, n));
  _persist();
}

export const expressionSettingsStore = {
  state,
  load,
  setCandidateCount,
};
