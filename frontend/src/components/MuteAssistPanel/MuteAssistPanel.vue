<script setup>
/**
 * 闭麦辅助输入面板。
 *
 * 单一职责：提供中文输入 → 英文表达的交互 UI。
 * 数据来源：muteAssistStore（由 expression-service 驱动）。
 */

import { ref, watch, nextTick, computed } from 'vue';
import { muteAssistStore } from '@/stores/mute-assist-store.js';
import { sceneStore } from '@/stores/scene-store.js';
import { expressionService } from '@/services/expression-service.js';
import SceneManager from './SceneManager.vue';

const inputRef = ref(null);
const outputRef = ref(null);
const localInput = ref('');
const showSceneManager = ref(false);

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

async function handleCopyCandidate(text) {
  try {
    await navigator.clipboard.writeText(text);
    store.markCopied();
  } catch (err) {
    console.error('[MuteAssistPanel] Copy candidate failed:', err);
  }
}

function onSceneChange(e) {
  sceneStore.setActive(e.target.value);
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

const hasCandidates = computed(() => store.state.candidates.length > 1);
const scenes = computed(() => sceneStore.state.scenes);
const activeSceneId = computed(() => sceneStore.state.activeSceneId);
</script>

<template>
  <div class="mute-panel" v-show="store.state.visible">
    <!-- 标题栏 -->
    <div class="panel-header">
      <span class="panel-title">闭麦表达助手</span>
      <div class="header-right">
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
        <button
          class="btn-icon btn-settings"
          :class="{ active: showSceneManager }"
          @click="showSceneManager = !showSceneManager"
          title="场景管理 & 设置"
        >⚙</button>
      </div>
    </div>

    <!-- 场景选择器 -->
    <div class="scene-bar" v-if="scenes.length > 0">
      <label class="scene-label">场景</label>
      <select class="scene-select" :value="activeSceneId" @change="onSceneChange">
        <option v-for="s in scenes" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
    </div>

    <!-- 场景管理弹窗 -->
    <SceneManager v-if="showSceneManager" @close="showSceneManager = false" />

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
        <!-- 多候选展示 -->
        <template v-else-if="hasCandidates && store.state.outputStatus === 'done'">
          <div
            v-for="(text, idx) in store.state.candidates"
            :key="idx"
            class="candidate-item"
          >
            <span class="candidate-number">{{ idx + 1 }}.</span>
            <span class="candidate-text">{{ text }}</span>
            <button
              class="btn-copy-candidate"
              @click="handleCopyCandidate(text)"
              title="复制此条"
            >📋</button>
          </div>
        </template>
        <!-- 单条展示 / streaming -->
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

<style scoped src="./MuteAssistPanel.scoped.css"></style>
