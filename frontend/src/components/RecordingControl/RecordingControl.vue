<script setup>
/**
 * 录音控制按钮 + 静音模式下拉。
 *
 * 单一职责：呈现录音开关与静音模式选择，委托 recordingStore 与后端同步。
 * 仅在会议进行中可用。
 */

import { watch } from 'vue';
import { recordingStore } from '@/stores/recording-store.js';

const props = defineProps({
  meetingActive: { type: Boolean, default: false },
});

const store = recordingStore;

// 会议结束时本地复位录音状态
watch(
  () => props.meetingActive,
  (active, prev) => {
    if (prev && !active) store.reset();
  },
);

function onModeChange(e) {
  store.setMode(e.target.value);
}
</script>

<template>
  <div class="recording-control" :class="{ disabled: !meetingActive }">
    <button
      class="rec-btn"
      :class="{ active: store.state.enabled }"
      :disabled="!meetingActive"
      :title="store.state.enabled ? '正在录音，点击暂停' : '开始录音'"
      @click="store.toggle()"
    >
      <span class="rec-dot" :class="{ on: store.state.enabled }"></span>
      {{ store.state.enabled ? '录音中' : '录音' }}
    </button>
    <select
      class="rec-mode"
      :value="store.state.mode"
      :disabled="!meetingActive"
      title="暂停/静音时的处理方式"
      @change="onModeChange"
    >
      <option value="silence">静音占位</option>
      <option value="skip">跳过不录</option>
    </select>
  </div>
</template>

<style scoped>
.recording-control {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.recording-control.disabled {
  opacity: 0.5;
}

.rec-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  padding: 3px 9px;
  border: 1px solid #ddd;
  border-radius: 4px;
  background: #fff;
  color: #666;
  cursor: pointer;
  transition: all 0.15s;
}

.rec-btn:hover:not(:disabled) {
  border-color: #ef9a9a;
  color: #c62828;
}

.rec-btn.active {
  background: #ffebee;
  border-color: #ef5350;
  color: #c62828;
}

.rec-btn:disabled {
  cursor: not-allowed;
}

.rec-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #bbb;
}

.rec-dot.on {
  background: #ef5350;
  animation: rec-pulse 1.2s ease-in-out infinite;
}

@keyframes rec-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

.rec-mode {
  font-size: 11px;
  padding: 2px 4px;
  border: 1px solid #ddd;
  border-radius: 4px;
  background: #fff;
  color: #555;
  cursor: pointer;
}

.rec-mode:disabled {
  cursor: not-allowed;
}
</style>
