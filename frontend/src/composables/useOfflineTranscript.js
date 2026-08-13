/**
 * 录音重解析字幕 — 对已结束会议的 recording.wav 重新离线转写，
 * 生成一份独立于实时字幕的「录音解析字幕」，并在回看时切换查看。
 *
 * 单一职责：管理离线字幕的加载 / 触发解析 / 进度 / 轨道切换。
 * 仅在回看模式（loadedMeetingId 非空）下有意义。
 */

import { ref, reactive } from 'vue';
import { summaryStore } from '@/stores/summary-store.js';
import { notificationStore } from '@/stores/notification-store.js';

/**
 * @param {Object} deps
 * @param {import('vue').Ref<Array>} deps.transcriptions 当前展示的字幕列表（会被切换）
 * @param {import('vue').Ref<string|null>} deps.loadedMeetingId 当前回看的会议 ID
 */
export function useOfflineTranscript({ transcriptions, loadedMeetingId }) {
  const activeTrack = ref('original'); // 'original' | 'offline'
  const hasOffline = ref(false);
  const progress = reactive({ running: false, ratio: 0 });

  let originalSnapshot = [];
  let offlineList = [];

  function _mapRaw(raw) {
    return (raw || []).map((t, i) => ({
      id: i + 1,
      text: t.text,
      language: t.language || '',
      speaker: t.speaker || '',
      source: t.source || '',
      timestamp: t.timestamp
        ? new Date(t.timestamp * 1000).toLocaleTimeString()
        : '',
      epoch: t.timestamp ? t.timestamp * 1000 : null,
      segmentStart: typeof t.segment_start === 'number' ? t.segment_start : null,
      segmentEnd: typeof t.segment_end === 'number' ? t.segment_end : null,
      annotations: [],
    }));
  }

  function _toReview(list) {
    return list.map((e) => ({
      text: e.text,
      timestamp: e.epoch ? e.epoch / 1000 : 0,
      start: e.segmentStart,
      end: e.segmentEnd,
    }));
  }

  function reset() {
    activeTrack.value = 'original';
    hasOffline.value = false;
    progress.running = false;
    progress.ratio = 0;
    originalSnapshot = [];
    offlineList = [];
  }

  // 载入会议后调用：快照原字幕，并探测是否已有离线字幕
  async function onMeetingLoaded(meetingId) {
    reset();
    originalSnapshot = transcriptions.value.slice();
    if (!meetingId || !window.electronAPI?.loadOfflineTranscript) return;
    try {
      const res = await window.electronAPI.loadOfflineTranscript(meetingId);
      if (res?.ok && res.transcriptions?.length) {
        offlineList = _mapRaw(res.transcriptions);
        hasOffline.value = true;
      }
    } catch (_) { /* 忽略：无离线字幕视为未生成 */ }
  }

  function switchTrack(track) {
    if (track === activeTrack.value) return;
    if (track === 'offline' && !hasOffline.value) return;
    // 离开原轨道前保存快照（回看模式下用户可能编辑过原字幕）
    if (activeTrack.value === 'original') originalSnapshot = transcriptions.value.slice();
    activeTrack.value = track;
    const list = (track === 'offline' ? offlineList : originalSnapshot).slice();
    transcriptions.value = list;
    // 同步给回看时间轴，使时段摘要基于当前展示的字幕
    summaryStore.setReviewData(_toReview(list), loadedMeetingId.value || '');
  }

  async function startRetranscribe() {
    const meetingId = loadedMeetingId.value;
    if (!meetingId || progress.running || !window.electronAPI?.sendControl) return;
    progress.running = true;
    progress.ratio = 0;
    await window.electronAPI.sendControl('retranscribe', { meeting_id: meetingId });
  }

  // 注册主进程进度/完成事件，返回清理函数
  function bind() {
    if (!window.electronAPI?.on) return () => {};
    const offProgress = window.electronAPI.on('python:retranscribe-progress', (d) => {
      if (!d || d.meeting_id !== loadedMeetingId.value) return;
      progress.running = true;
      progress.ratio = d.ratio || 0;
    });
    const offDone = window.electronAPI.on('python:retranscribe-done', async (d) => {
      if (!d || d.meeting_id !== loadedMeetingId.value) return;
      progress.running = false;
      progress.ratio = d.ok ? 1 : 0;
      if (!d.ok) {
        notificationStore.notifyError('录音解析失败：' + (d.error || '未知错误'));
        return;
      }
      try {
        const res = await window.electronAPI.loadOfflineTranscript(d.meeting_id);
        if (res?.ok && res.transcriptions?.length) {
          offlineList = _mapRaw(res.transcriptions);
          hasOffline.value = true;
          switchTrack('offline');
        }
      } catch (_) { /* 忽略 */ }
    });
    return () => { offProgress?.(); offDone?.(); };
  }

  return {
    activeTrack, hasOffline, progress,
    onMeetingLoaded, switchTrack, startRetranscribe, bind, reset,
  };
}
