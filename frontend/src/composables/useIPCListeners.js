/**
 * IPC 监听器注册 — 从 App.vue onMounted 提取。
 *
 * 集中管理 Electron IPC 事件监听的注册与清理。
 */

import { summaryStore } from '@/stores/summary-store.js';
import { muteAssistStore } from '@/stores/mute-assist-store.js';
import { expressionService } from '@/services/expression-service.js';

/**
 * @param {Object} deps
 * @param {import('vue').Ref<string>} deps.status
 * @param {import('vue').Ref<boolean>} deps.meetingActive
 * @param {import('vue').Ref<boolean>} deps.meetingStopping
 * @param {import('vue').Ref<Object|null>} deps.lastMeetingInfo
 * @param {import('vue').Ref<Array>} deps.transcriptions
 * @param {Function} deps.showError
 * @param {Function} deps.handleMenuAction
 * @param {Function} deps.syncAudioStateToMenu
 */
export function useIPCListeners({ status, meetingActive, meetingStopping, lastMeetingInfo, transcriptions, showError, handleMenuAction, syncAudioStateToMenu }) {

  const cleanupFns = [];

  async function setup() {
    if (!window.electronAPI) {
      status.value = 'no-electron';
      return;
    }

    cleanupFns.push(
      window.electronAPI.on('python:status', async (data) => {
        const prevActive = meetingActive.value;
        status.value = data.state || 'unknown';
        meetingActive.value = data.state === 'running' || data.state === 'stopping';

        if (data.state === 'stopping') {
          meetingStopping.value = true;
        }

        if (data.state === 'stopped') {
          meetingStopping.value = false;
        }

        if (data.state === 'running' && data.meeting_id) {
          summaryStore.setLiveMeetingId(data.meeting_id);
        }

        if (data.state === 'stopped' && prevActive) {
          const mid = data.meeting_id || summaryStore.state.liveMeetingId;
          if (mid && summaryStore.hasSummaryContent.value && window.electronAPI?.saveMeetingSummaries) {
            try {
              const saveData = summaryStore.getSummariesForSave();
              await window.electronAPI.saveMeetingSummaries(mid, saveData);
              summaryStore.markSaved();
            } catch (err) {
              console.error('[App] Auto-save summaries failed:', err);
            }
          }
          summaryStore.setLiveMeetingId('');

          // 会议结束后设置上次会议信息，显示“继续/新建”按钮
          if (transcriptions.value.length > 0) {
            lastMeetingInfo.value = {
              meeting_id: mid,
              transcription_count: transcriptions.value.length,
              has_summary: summaryStore.hasSummaryContent.value,
            };
          }
        }
      }),
    );

    cleanupFns.push(
      window.electronAPI.on('python:transcription', (data) => {
        transcriptions.value.push({
          id: Date.now(),
          text: data.text,
          language: data.language || '',
          speaker: data.speaker || '',
          source: data.source || '',
          timestamp: new Date().toLocaleTimeString(),
        });
        summaryStore.addLiveTranscription({
          text: data.text,
          timestamp: Date.now() / 1000,
        });
      }),
    );

    cleanupFns.push(
      window.electronAPI.on('python:error', (data) => {
        console.error('[Python Error]', data);
        showError(data.message || data.code || '后端错误');
      }),
    );

    cleanupFns.push(
      window.electronAPI.on('python:segment-summary', (data) => {
        summaryStore.addSegmentSummary(data);
      }),
    );

    cleanupFns.push(
      window.electronAPI.on('python:global-summary', (data) => {
        summaryStore.updateGlobalSummary(data);
      }),
    );

    cleanupFns.push(
      window.electronAPI.on('mute-panel:toggle', () => {
        muteAssistStore.toggleVisible();
      }),
    );

    cleanupFns.push(
      window.electronAPI.on('menu:action', (action, data) => {
        handleMenuAction(action, data);
      }),
    );

    syncAudioStateToMenu();

    try {
      const result = await window.electronAPI.getLLMConfig();
      if (result.ok && result.config) {
        expressionService.init(result.config);
      }
    } catch (err) {
      console.error('[App] Failed to load LLM config:', err);
    }
  }

  function cleanup() {
    cleanupFns.forEach((fn) => fn());
    cleanupFns.length = 0;
  }

  return { setup, cleanup };
}
