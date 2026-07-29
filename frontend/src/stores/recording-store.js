/**
 * 录音控制状态。
 *
 * 单一职责：维护录音开关与静音模式，并通过 IPC 同步给后端录音器。
 * 不含 UI；由 RecordingControl 组件消费。
 */

import { reactive } from 'vue';

const state = reactive({
  /** 是否正在录音（false = 静音，按 mode 决定跳过或写入静音占位） */
  enabled: false,
  /** 静音模式：'silence' 写入静音占位 | 'skip' 跳过不录 */
  mode: 'silence',
});

/** 把当前状态下发给后端录音器。 */
function _sync() {
  window.electronAPI?.sendControl?.('set_recording', {
    recording: state.enabled,
    mode: state.mode,
  });
}

function setEnabled(enabled) {
  state.enabled = !!enabled;
  _sync();
}

function toggle() {
  setEnabled(!state.enabled);
}

function setMode(mode) {
  if (mode !== 'silence' && mode !== 'skip') return;
  state.mode = mode;
  _sync();
}

/** 会议结束后本地复位（不下发，后端已随会议关闭录音器）。 */
function reset() {
  state.enabled = false;
  state.mode = 'silence';
}

export const recordingStore = { state, setEnabled, toggle, setMode, reset };
