/**
 * 工作区脏状态追踪。
 *
 * 统一管理「工作区是否有未保存变更」的标志，
 * 变更来源：摘要、字幕编辑、闭麦助手等。
 * 由全局保存按钮（工具栏 / 菜单 Ctrl+S）消费。
 */

import { reactive, computed } from 'vue';
import { summaryStore } from './summary-store.js';

const state = reactive({
  /** 字幕是否有用户编辑（非 ASR 原始结果） */
  transcriptionEdited: false,
});

/**
 * 工作区是否有任何未保存的变更。
 * 任一来源为 dirty 即返回 true。
 */
const isDirty = computed(() => {
  return summaryStore.isDirty.value || state.transcriptionEdited;
});

/**
 * 是否存在可保存的会议上下文。
 */
const canSave = computed(() => {
  return !!summaryStore.activeMeetingId.value && isDirty.value;
});

/**
 * 标记字幕有用户编辑。
 */
function markTranscriptionEdited() {
  state.transcriptionEdited = true;
}

/**
 * 全部标记为已保存。
 */
function markAllSaved() {
  summaryStore.markSaved();
  state.transcriptionEdited = false;
}

/**
 * 重置（新会议 / 清空时调用）。
 */
function reset() {
  state.transcriptionEdited = false;
}

export const workspaceStore = {
  state,
  isDirty,
  canSave,
  markTranscriptionEdited,
  markAllSaved,
  reset,
};
