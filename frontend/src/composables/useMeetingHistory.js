/**
 * 会议历史相关逻辑 — 从 App.vue 提取。
 *
 * 包含：未保存摘要检查、历史面板切换、加载/删除会议、返回实时。
 */

import { nextTick } from 'vue';
import { summaryStore } from '@/stores/summary-store.js';
import { workspaceStore } from '@/stores/workspace-store.js';

/**
 * @param {Object} deps
 * @param {import('vue').Ref<Array>} deps.transcriptions
 * @param {import('vue').Ref<boolean>} deps.historyVisible
 * @param {import('vue').Ref} deps.historyPanelRef
 * @param {import('vue').Ref<string|null>} deps.loadedMeetingId
 */
export function useMeetingHistory({ transcriptions, historyVisible, historyPanelRef, loadedMeetingId }) {

  async function checkUnsavedSummary() {
    const meetingId = summaryStore.activeMeetingId.value;
    if (!workspaceStore.isDirty.value || !meetingId) return true;

    const action = window.confirm(
      '当前工作区有未保存的修改，是否保存？\n\n点击"确定"保存后继续，点击"取消"放弃修改。',
    );
    if (action) {
      if (window.electronAPI?.saveMeetingSummaries) {
        const data = summaryStore.getSummariesForSave();
        const result = await window.electronAPI.saveMeetingSummaries(meetingId, data);
        if (result.ok) {
          workspaceStore.markAllSaved();
        } else {
          window.alert('保存失败：' + (result.error || '未知错误'));
          return false;
        }
      }
    }
    return true;
  }

  async function toggleHistory() {
    historyVisible.value = !historyVisible.value;
    if (historyVisible.value) {
      nextTick(() => historyPanelRef.value?.refresh());
    }
  }

  async function loadMeeting(meetingId) {
    if (!window.electronAPI?.loadMeeting) return;

    if (!(await checkUnsavedSummary())) return;

    try {
      const result = await window.electronAPI.loadMeeting(meetingId);
      if (!result.ok) {
        console.error('[App] Failed to load meeting:', result.error);
        return;
      }
      const { meta, transcriptions: trans, summaries } = result.data;

      transcriptions.value = (trans || []).map((t, i) => ({
        id: i + 1,
        text: t.text,
        language: t.language || '',
        speaker: t.speaker || '',
        source: t.source || '',
        timestamp: t.timestamp
          ? new Date(t.timestamp * 1000).toLocaleTimeString()
          : '',
      }));

      summaryStore.clearAll();

      summaryStore.setReviewData(
        (trans || []).map((t) => ({ text: t.text, timestamp: t.timestamp || 0 })),
        meetingId,
      );

      if (summaries?.segments) {
        for (const seg of summaries.segments) {
          summaryStore.addSegmentSummary(seg);
        }
      }
      if (summaries?.global_summary) {
        summaryStore.updateGlobalSummary({
          ...summaries.global_summary,
          action_items: summaries.action_items || [],
        });
      }

      summaryStore.markSaved();

      if (!summaryStore.state.visible) {
        summaryStore.toggleVisible();
      }

      loadedMeetingId.value = meetingId;
      historyVisible.value = false;
    } catch (err) {
      console.error('[App] Failed to load meeting:', err);
    }
  }

  async function deleteMeeting(meetingId) {
    if (!window.electronAPI?.deleteMeeting) return;
    if (!window.confirm('确定要删除此会议记录？此操作不可恢复。')) return;
    try {
      const result = await window.electronAPI.deleteMeeting(meetingId);
      if (result.ok) {
        historyPanelRef.value?.refresh();
        if (loadedMeetingId.value === meetingId) {
          loadedMeetingId.value = null;
          transcriptions.value = [];
          summaryStore.clearAll();
        }
      }
    } catch (err) {
      console.error('[App] Failed to delete meeting:', err);
    }
  }

  async function backToLive() {
    if (!(await checkUnsavedSummary())) return;
    loadedMeetingId.value = null;
    transcriptions.value = [];
    summaryStore.clearAll();
  }

  return { checkUnsavedSummary, toggleHistory, loadMeeting, deleteMeeting, backToLive };
}
