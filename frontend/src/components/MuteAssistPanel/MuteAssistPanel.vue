<script setup>
/**
 * 闭麦辅助输入面板。
 *
 * 单一职责：提供中文输入 → 英文表达的交互 UI。
 * 数据来源：muteAssistStore（由 expression-service 驱动）。
 */

import { ref, watch, nextTick, computed } from 'vue';
import { muteAssistStore } from '@/stores/mute-assist-store.js';
import { expressionService } from '@/services/expression-service.js';

const inputRef = ref(null);
const outputRef = ref(null);
const localInput = ref('');

const store = muteAssistStore;

// 自动滚动输出区到底部
watch(
  () => store.state.outputText,
  async () => {
    await nextTick();
    if (outputRef.value) {
      outputRef.value.scrollTop = outputRef.value.scrollHeight;
    }
  },
);

// ---------------------------------------------------------------
// 操作
// ---------------------------------------------------------------

function handleSubmit() {
  const text = localInput.value.trim();
  if (!text || store.isStreaming.value) return;

  expressionService.express(text);
}

function handleKeydown(e) {
  // Enter 提交，Shift+Enter 换行
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    handleSubmit();
  }
}

async function handleCopy() {
  const text = store.state.outputText.trim();
  if (!text) return;

  try {
    await navigator.clipboard.writeText(text);
    store.markCopied();
  } catch (err) {
    console.error('[MuteAssistPanel] Copy failed:', err);
  }
}

function handleClear() {
  localInput.value = '';
  store.resetOutput();
  inputRef.value?.focus();
}

function handleAbort() {
  expressionService.abort();
  store.resetOutput();
}

function switchMode(mode) {
  store.setInputMode(mode);
}

function handleMicToggle() {
  if (store.state.micStatus === 'idle') {
    store.setMicStatus('recording');
    // 实际麦克风录音由后续任务实现，此处仅做 UI 状态切换
  } else if (store.state.micStatus === 'recording') {
    store.setMicStatus('processing');
    // 模拟处理完成
    setTimeout(() => {
      store.setMicStatus('idle');
    }, 500);
  }
}

function selectHistory(entry) {
  localInput.value = entry.input;
  store.startExpression(entry.input);
  store.appendOutput(store.state.activeId, entry.output);
  store.finishExpression(store.state.activeId);
}

// ---------------------------------------------------------------
// 计算属性
// ---------------------------------------------------------------

const micButtonText = computed(() => {
  switch (store.state.micStatus) {
    case 'recording': return '⏹ 停止';
    case 'processing': return '⏳ 处理中';
    default: return '🎤 录音';
  }
});

const micButtonClass = computed(() => {
  return store.state.micStatus === 'recording' ? 'recording' : '';
});

const showHistory = computed(() => store.state.history.length > 0);
</script>

<template>
  <div class="mute-panel" v-show="store.state.visible">
    <!-- 标题栏 -->
    <div class="panel-header">
      <span class="panel-title">闭麦表达助手</span>
      <div class="mode-switch">
        <button
          class="mode-btn"
          :class="{ active: store.state.inputMode === 'keyboard' }"
          @click="switchMode('keyboard')"
        >
          ⌨️ 键盘
        </button>
        <button
          class="mode-btn"
          :class="{ active: store.state.inputMode === 'mic' }"
          @click="switchMode('mic')"
        >
          🎤 麦克风
        </button>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="input-section">
      <!-- 键盘模式 -->
      <div v-if="store.state.inputMode === 'keyboard'" class="keyboard-input">
        <textarea
          ref="inputRef"
          v-model="localInput"
          class="input-box"
          placeholder="输入中文，按 Enter 发送…"
          rows="3"
          @keydown="handleKeydown"
          :disabled="store.isStreaming.value"
        ></textarea>
        <div class="input-actions">
          <button
            class="btn-send"
            @click="handleSubmit"
            :disabled="!localInput.trim() || store.isStreaming.value"
          >
            发送
          </button>
        </div>
      </div>

      <!-- 麦克风模式 -->
      <div v-else class="mic-input">
        <button
          class="btn-mic"
          :class="micButtonClass"
          @click="handleMicToggle"
          :disabled="store.state.micStatus === 'processing'"
        >
          {{ micButtonText }}
        </button>
        <p class="mic-hint">
          <template v-if="store.state.micStatus === 'idle'">
            点击开始录音，说中文
          </template>
          <template v-else-if="store.state.micStatus === 'recording'">
            正在录音，点击停止…
          </template>
          <template v-else>
            正在处理语音…
          </template>
        </p>
      </div>
    </div>

    <!-- 输出区 -->
    <div class="output-section">
      <div class="output-header">
        <span class="output-label">英文表达</span>
        <div class="output-actions">
          <button
            v-if="store.isStreaming.value"
            class="btn-icon btn-abort"
            @click="handleAbort"
            title="取消"
          >
            ✕
          </button>
          <button
            class="btn-icon btn-copy"
            @click="handleCopy"
            :disabled="!store.hasCopyableOutput.value"
            :title="store.state.copied ? '已复制' : '复制'"
          >
            {{ store.state.copied ? '✓' : '📋' }}
          </button>
          <button
            class="btn-icon btn-clear"
            @click="handleClear"
            title="清空"
            :disabled="store.isStreaming.value"
          >
            🗑
          </button>
        </div>
      </div>
      <div ref="outputRef" class="output-box">
        <template v-if="store.state.outputStatus === 'idle' && !store.state.outputText">
          <span class="output-placeholder">英文结果将显示在这里…</span>
        </template>
        <template v-else-if="store.state.outputStatus === 'error'">
          <span class="output-error">请求失败，请检查 LLM 配置后重试</span>
        </template>
        <template v-else>
          <span class="output-text">{{ store.state.outputText }}</span>
          <span
            v-if="store.state.outputStatus === 'streaming'"
            class="typing-cursor"
          >▍</span>
        </template>
      </div>
    </div>

    <!-- 历史记录 -->
    <div v-if="showHistory" class="history-section">
      <div class="history-header">
        <span class="history-label">历史记录</span>
        <button class="btn-clear-history" @click="store.clearHistory">清空</button>
      </div>
      <div class="history-list">
        <div
          v-for="entry in store.state.history.slice().reverse().slice(0, 5)"
          :key="entry.id"
          class="history-item"
          @click="selectHistory(entry)"
        >
          <div class="history-input">{{ entry.input }}</div>
          <div class="history-output">{{ entry.output }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mute-panel {
  display: flex;
  flex-direction: column;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif;
}

/* 标题栏 */
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #f5f5f5;
  border-bottom: 1px solid #e0e0e0;
}

.panel-title {
  font-size: 13px;
  font-weight: 600;
  color: #333;
}

.mode-switch {
  display: flex;
  gap: 2px;
  background: #e0e0e0;
  border-radius: 4px;
  padding: 2px;
}

.mode-btn {
  font-size: 11px;
  padding: 2px 8px;
  border: none;
  border-radius: 3px;
  background: transparent;
  color: #666;
  cursor: pointer;
  transition: all 0.15s;
}

.mode-btn.active {
  background: #fff;
  color: #333;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
}

/* 输入区 */
.input-section {
  padding: 10px 12px 6px;
}

.input-box {
  width: 100%;
  resize: none;
  border: 1px solid #ddd;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 14px;
  font-family: inherit;
  line-height: 1.5;
  outline: none;
  transition: border-color 0.2s;
}

.input-box:focus {
  border-color: #90caf9;
}

.input-box:disabled {
  background: #f9f9f9;
  color: #999;
}

.input-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 6px;
}

.btn-send {
  font-size: 13px;
  padding: 5px 16px;
  border: none;
  border-radius: 4px;
  background: #1976d2;
  color: #fff;
  cursor: pointer;
  transition: background 0.15s;
}

.btn-send:hover:not(:disabled) {
  background: #1565c0;
}

.btn-send:disabled {
  background: #bbb;
  cursor: not-allowed;
}

/* 麦克风模式 */
.mic-input {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 12px 0;
}

.btn-mic {
  width: 80px;
  height: 80px;
  border-radius: 50%;
  border: 2px solid #ddd;
  background: #f5f5f5;
  font-size: 16px;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-mic:hover:not(:disabled) {
  border-color: #90caf9;
  background: #e3f2fd;
}

.btn-mic.recording {
  border-color: #f44336;
  background: #ffebee;
  animation: pulse-mic 1.5s infinite;
}

.btn-mic:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

@keyframes pulse-mic {
  0%, 100% { box-shadow: 0 0 0 0 rgba(244, 67, 54, 0.3); }
  50% { box-shadow: 0 0 0 12px rgba(244, 67, 54, 0); }
}

.mic-hint {
  font-size: 12px;
  color: #888;
}

/* 输出区 */
.output-section {
  padding: 0 12px 10px;
}

.output-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.output-label {
  font-size: 12px;
  color: #888;
  font-weight: 500;
}

.output-actions {
  display: flex;
  gap: 4px;
}

.btn-icon {
  width: 28px;
  height: 28px;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
  background: #fff;
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}

.btn-icon:hover:not(:disabled) {
  background: #f5f5f5;
  border-color: #ccc;
}

.btn-icon:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-abort {
  color: #f44336;
  border-color: #ffcdd2;
}

.btn-abort:hover {
  background: #ffebee !important;
}

.output-box {
  min-height: 60px;
  max-height: 120px;
  overflow-y: auto;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  padding: 8px 10px;
  background: #fafafa;
  font-size: 14px;
  line-height: 1.5;
}

.output-placeholder {
  color: #bbb;
  font-style: italic;
}

.output-error {
  color: #f44336;
}

.output-text {
  color: #1565c0;
  word-break: break-word;
}

.typing-cursor {
  animation: blink 0.8s infinite;
  color: #1565c0;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* 历史记录 */
.history-section {
  border-top: 1px solid #e8e8e8;
  padding: 8px 12px;
}

.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.history-label {
  font-size: 11px;
  color: #999;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.btn-clear-history {
  font-size: 11px;
  padding: 1px 6px;
  border: 1px solid #ddd;
  border-radius: 3px;
  background: #fff;
  color: #999;
  cursor: pointer;
}

.btn-clear-history:hover {
  background: #f5f5f5;
}

.history-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 120px;
  overflow-y: auto;
}

.history-item {
  padding: 4px 8px;
  border-radius: 4px;
  background: #f9f9f9;
  cursor: pointer;
  transition: background 0.15s;
}

.history-item:hover {
  background: #e3f2fd;
}

.history-input {
  font-size: 12px;
  color: #666;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.history-output {
  font-size: 12px;
  color: #1565c0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
