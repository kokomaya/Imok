<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import { useHashRoute } from '@/router.js';
import { SubtitleOverlay } from '@/components/SubtitleOverlay';
import { MuteAssistPanel } from '@/components/MuteAssistPanel';
import { SummaryPanel } from '@/components/SummaryPanel';
import { muteAssistStore } from '@/stores/mute-assist-store.js';
import { summaryStore } from '@/stores/summary-store.js';
import { expressionService } from '@/services/expression-service.js';

const { currentRoute } = useHashRoute();

const status = ref('disconnected');
const transcriptions = ref([]);
const audioSource = ref('wasapi');

let cleanupFns = [];

onMounted(async () => {
  // 主窗口视图才直接监听 IPC（overlay 有独立的 ipc-bridge 服务）
  if (currentRoute.value !== 'main') return;
  if (!window.electronAPI) {
    status.value = 'no-electron';
    return;
  }

  cleanupFns.push(
    window.electronAPI.on('python:status', (data) => {
      status.value = data.state || 'unknown';
      if (data.source) {
        audioSource.value = data.source;
      }
    }),
  );

  cleanupFns.push(
    window.electronAPI.on('python:transcription', (data) => {
      transcriptions.value.push({
        id: Date.now(),
        text: data.text,
        language: data.language || '',
        timestamp: new Date().toLocaleTimeString(),
      });
    }),
  );

  cleanupFns.push(
    window.electronAPI.on('python:error', (data) => {
      console.error('[Python Error]', data);
    }),
  );

  cleanupFns.push(
    window.electronAPI.on('mute-panel:toggle', () => {
      muteAssistStore.toggleVisible();
    }),
  );

  // 初始化 LLM 配置 → 表达服务
  try {
    const result = await window.electronAPI.getLLMConfig();
    if (result.ok && result.config) {
      expressionService.init(result.config);
      console.log('[App] Expression service initialized');
    } else {
      console.warn('[App] LLM config not available:', result.error);
    }
  } catch (err) {
    console.error('[App] Failed to load LLM config:', err);
  }
});

onUnmounted(() => {
  cleanupFns.forEach((fn) => fn());
  cleanupFns = [];
});

function openOverlay() {
  if (window.electronAPI) {
    window.electronAPI.openOverlay();
  }
}

async function switchAudioSource() {
  if (!window.electronAPI) return;
  const next = audioSource.value === 'wasapi' ? 'mic' : 'wasapi';
  await window.electronAPI.sendControl('switch_source', { source: next });
}
</script>

<template>
  <!-- 悬浮窗视图 -->
  <SubtitleOverlay v-if="currentRoute === 'overlay'" />

  <!-- 主窗口视图 -->
  <div v-else class="app">
    <header class="header">
      <h1 class="title">Imok Meeting Assistant</h1>
      <div class="header-actions">
        <button
          class="btn-audio-source"
          :class="audioSource"
          @click="switchAudioSource"
          :title="audioSource === 'wasapi' ? '当前：系统音频（点击切换到麦克风）' : '当前：麦克风（点击切换到系统音频）'"
        >
          {{ audioSource === 'wasapi' ? '🔊 系统音频' : '🎤 麦克风' }}
        </button>
        <button class="btn-overlay" @click="openOverlay" title="打开悬浮字幕">
          字幕窗
        </button>
        <button
          class="btn-mute-panel"
          @click="muteAssistStore.toggleVisible()"
          title="闭麦表达助手 (Ctrl+Shift+M)"
        >
          闭麦助手
        </button>
        <button
          class="btn-summary"
          @click="summaryStore.toggleVisible()"
          title="会议摘要面板"
        >
          摘要
        </button>
        <span class="status-badge" :class="status">{{ status }}</span>
      </div>
    </header>

    <main class="content">
      <!-- 会议摘要面板 -->
      <SummaryPanel />

      <!-- 闭麦表达助手 -->
      <MuteAssistPanel />

      <section class="transcription-panel">
        <h2>实时字幕</h2>
        <div class="transcription-list">
          <p v-if="transcriptions.length === 0" class="placeholder">
            等待语音输入…
          </p>
          <div
            v-for="item in transcriptions"
            :key="item.id"
            class="transcription-item"
          >
            <span class="time">{{ item.timestamp }}</span>
            <span class="lang" v-if="item.language">[{{ item.language }}]</span>
            <span class="text">{{ item.text }}</span>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #e0e0e0;
  background: #fafafa;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.btn-overlay {
  font-size: 12px;
  padding: 4px 10px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  color: #333;
  cursor: pointer;
}

.btn-overlay:hover {
  background: #e3f2fd;
  border-color: #90caf9;
}

.btn-mute-panel {
  font-size: 12px;
  padding: 4px 10px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  color: #333;
  cursor: pointer;
}

.btn-mute-panel:hover {
  background: #fff3e0;
  border-color: #ffb74d;
}

.btn-summary {
  font-size: 12px;
  padding: 4px 10px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  color: #333;
  cursor: pointer;
}

.btn-summary:hover {
  background: #e8f5e9;
  border-color: #81c784;
}

.btn-audio-source {
  font-size: 12px;
  padding: 4px 10px;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  color: #333;
  cursor: pointer;
}

.btn-audio-source.wasapi {
  background: #e8eaf6;
  border-color: #7986cb;
  color: #283593;
}

.btn-audio-source.mic {
  background: #fce4ec;
  border-color: #f48fb1;
  color: #880e4f;
}

.title {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
}

.status-badge {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  background: #e0e0e0;
  color: #666;
}

.status-badge.ready {
  background: #e8f5e9;
  color: #2e7d32;
}

.status-badge.running {
  background: #e3f2fd;
  color: #1565c0;
}

.status-badge.error {
  background: #ffebee;
  color: #c62828;
}

.content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.transcription-panel h2 {
  font-size: 14px;
  color: #555;
  margin: 0 0 12px 0;
}

.transcription-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.placeholder {
  color: #999;
  font-style: italic;
}

.transcription-item {
  display: flex;
  gap: 8px;
  font-size: 14px;
  line-height: 1.5;
}

.time {
  color: #999;
  font-size: 12px;
  flex-shrink: 0;
}

.lang {
  color: #1565c0;
  font-size: 12px;
  flex-shrink: 0;
}

.text {
  color: #333;
}
</style>
