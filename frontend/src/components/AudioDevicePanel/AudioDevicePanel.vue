<script setup>
import { ref, reactive, onMounted, onUnmounted, computed, watch } from 'vue';

const props = defineProps({
  visible: { type: Boolean, default: false },
});
const emit = defineEmits(['close', 'select-device']);

// ── 设备数据 ──
const devices = reactive({ loopback: [], input: [] });
const loading = ref(false);
const error = ref('');

// ── 当前活跃设备（从 main.js 传来的选择状态）
const selectedLoopback = ref(null);
const selectedMic = ref(null);

// ── 实时电平数据 { wasapi: 0.03, mic: 0.01 }
const levels = reactive({});

// ── 电平历史（用于波形绘制，每个源最近 32 个 RMS 采样）
const WAVE_LEN = 32;
const waveHistory = reactive({});

// ── 测试结果内联显示 { 'loopback-5': { text, icon, timer } }
const testResults = reactive({});
const testingDevices = reactive({});

let cleanupLevelListener = null;

async function fetchDevices() {
  if (!window.electronAPI?.listAudioDevices) return;
  loading.value = true;
  error.value = '';
  try {
    const result = await window.electronAPI.listAudioDevices();
    if (result.ok) {
      devices.loopback = result.loopback || [];
      devices.input = result.input || [];
    } else {
      error.value = result.error || '获取设备列表失败';
    }
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

function onLevelData(data) {
  const lvls = data?.levels || {};
  for (const [key, val] of Object.entries(lvls)) {
    levels[key] = val;
    if (!waveHistory[key]) {
      waveHistory[key] = new Array(WAVE_LEN).fill(0);
    }
    waveHistory[key].push(val);
    if (waveHistory[key].length > WAVE_LEN) {
      waveHistory[key].shift();
    }
  }
}

function selectDevice(type, index) {
  if (type === 'loopback') {
    selectedLoopback.value = index;
  } else {
    selectedMic.value = index;
  }
  emit('select-device', { type, index });

  if (window.electronAPI?.sendControl) {
    window.electronAPI.sendControl('set_devices', {
      loopback_device: selectedLoopback.value,
      mic_device: selectedMic.value,
    });
  }
}

async function testDevice(type, index) {
  if (!window.electronAPI?.testAudioDevice) return;
  const key = `${type}-${index}`;

  // 清除之前的结果
  if (testResults[key]?.timer) clearTimeout(testResults[key].timer);
  testingDevices[key] = true;
  delete testResults[key];

  try {
    const result = await window.electronAPI.testAudioDevice({ type, index, seconds: 2 });
    testingDevices[key] = false;
    if (result.ok) {
      const peakDb = result.peak > 0 ? (20 * Math.log10(result.peak)).toFixed(1) : '-∞';
      const icon = result.hasSignal ? '✅' : '⚠️';
      const text = result.hasSignal ? `${peakDb} dB` : '无信号';
      showTestResult(key, icon, text);
    } else {
      showTestResult(key, '❌', result.error || '失败');
    }
  } catch (e) {
    testingDevices[key] = false;
    showTestResult(key, '❌', e.message);
  }
}

function showTestResult(key, icon, text) {
  const timer = setTimeout(() => { delete testResults[key]; }, 4000);
  testResults[key] = { icon, text, timer };
}

function getWavePath(sourceKey) {
  const hist = waveHistory[sourceKey];
  if (!hist || hist.length < 2) return '';
  const w = 120;
  const h = 22;
  const step = w / (WAVE_LEN - 1);
  const toY = (rms) => {
    if (rms <= 0) return h;
    const db = 20 * Math.log10(Math.max(rms, 1e-6));
    const norm = Math.max(0, Math.min(1, (db + 60) / 60));
    return h - norm * (h - 2);
  };

  let d = `M0,${toY(hist[0])}`;
  for (let i = 1; i < hist.length; i++) {
    const x = i * step;
    const y = toY(hist[i]);
    d += ` L${x.toFixed(1)},${y.toFixed(1)}`;
  }
  return d;
}

function getLevelPercent(sourceKey) {
  const rms = levels[sourceKey] || 0;
  if (rms <= 0) return 0;
  const db = 20 * Math.log10(Math.max(rms, 1e-6));
  return Math.max(0, Math.min(100, ((db + 60) / 60) * 100));
}

function getLevelColor(sourceKey) {
  const pct = getLevelPercent(sourceKey);
  if (pct > 85) return '#ef5350';
  if (pct > 50) return '#ff9800';
  if (pct > 10) return '#4caf50';
  return '#bdbdbd';
}

// 映射 section 到 level source key
function sectionLevelKey(type) {
  return type === 'loopback' ? 'wasapi' : 'mic';
}

function hasLevel(type) {
  const key = sectionLevelKey(type);
  return levels[key] !== undefined;
}

watch(() => props.visible, (v) => {
  if (v) fetchDevices();
});

onMounted(() => {
  if (window.electronAPI?.on) {
    cleanupLevelListener = window.electronAPI.on('python:audio-level', onLevelData);
  }
  if (props.visible) fetchDevices();
});

onUnmounted(() => {
  if (cleanupLevelListener) cleanupLevelListener();
  // 清理所有测试结果的定时器
  for (const r of Object.values(testResults)) {
    if (r?.timer) clearTimeout(r.timer);
  }
});
</script>

<template>
  <div v-if="visible" class="adp">
    <div class="adp-header">
      <span class="adp-title">🎛 音频设备</span>
      <div class="adp-header-actions">
        <button class="adp-btn-small" @click="fetchDevices" :disabled="loading" title="刷新设备列表">🔄</button>
        <button class="adp-close" @click="$emit('close')">✕</button>
      </div>
    </div>

    <div v-if="error" class="adp-error">{{ error }}</div>
    <div v-if="loading" class="adp-loading">扫描设备…</div>

    <!-- ═══ 系统音频 ═══ -->
    <div class="adp-section">
      <div class="adp-section-header">
        <span class="adp-section-title">🔊 系统音频</span>
        <!-- 活跃时的实时电平 -->
        <div v-if="hasLevel('loopback')" class="adp-inline-monitor">
          <div class="adp-level-bar-container">
            <div class="adp-level-bar" :style="{ width: getLevelPercent('wasapi') + '%', background: getLevelColor('wasapi') }"></div>
          </div>
          <svg class="adp-wave" viewBox="0 0 120 22" preserveAspectRatio="none">
            <path :d="getWavePath('wasapi')" fill="none" :stroke="getLevelColor('wasapi')" stroke-width="1.5" />
          </svg>
        </div>
        <span v-else class="adp-monitor-placeholder">未活跃</span>
      </div>

      <div v-if="devices.loopback.length === 0" class="adp-empty">无可用设备</div>
      <div
        v-for="dev in devices.loopback"
        :key="'lb-' + dev.index"
        class="adp-device"
        :class="{ active: selectedLoopback === dev.index }"
        @click="selectDevice('loopback', dev.index)"
      >
        <div class="adp-device-main">
          <span class="adp-device-name">
            {{ dev.name }}
            <span v-if="dev.isDefault" class="adp-default-badge">默认</span>
          </span>
          <span class="adp-device-meta">{{ dev.hostApi }} · {{ dev.sampleRate }}Hz · {{ dev.maxInputChannels }}ch</span>
        </div>
        <div class="adp-device-actions">
          <!-- 测试结果内联 -->
          <transition name="adp-fade">
            <span v-if="testResults[`loopback-${dev.index}`]" class="adp-test-result">
              {{ testResults[`loopback-${dev.index}`].icon }} {{ testResults[`loopback-${dev.index}`].text }}
            </span>
          </transition>
          <span v-if="testingDevices[`loopback-${dev.index}`]" class="adp-testing">测试中…</span>
          <span v-if="selectedLoopback === dev.index" class="adp-active-indicator" title="当前选中">✓</span>
          <button
            class="adp-btn-test"
            @click.stop="testDevice('loopback', dev.index)"
            :disabled="testingDevices[`loopback-${dev.index}`]"
            title="测试此设备"
          >测试</button>
        </div>
      </div>
    </div>

    <!-- ═══ 麦克风 ═══ -->
    <div class="adp-section">
      <div class="adp-section-header">
        <span class="adp-section-title">🎤 麦克风</span>
        <div v-if="hasLevel('mic')" class="adp-inline-monitor">
          <div class="adp-level-bar-container">
            <div class="adp-level-bar" :style="{ width: getLevelPercent('mic') + '%', background: getLevelColor('mic') }"></div>
          </div>
          <svg class="adp-wave" viewBox="0 0 120 22" preserveAspectRatio="none">
            <path :d="getWavePath('mic')" fill="none" :stroke="getLevelColor('mic')" stroke-width="1.5" />
          </svg>
        </div>
        <span v-else class="adp-monitor-placeholder">未活跃</span>
      </div>

      <div v-if="devices.input.length === 0" class="adp-empty">无可用设备</div>
      <div
        v-for="dev in devices.input"
        :key="'in-' + dev.index"
        class="adp-device"
        :class="{ active: selectedMic === dev.index }"
        @click="selectDevice('mic', dev.index)"
      >
        <div class="adp-device-main">
          <span class="adp-device-name">
            {{ dev.name }}
            <span v-if="dev.isDefault" class="adp-default-badge">默认</span>
          </span>
          <span class="adp-device-meta">{{ dev.hostApi }} · {{ dev.sampleRate }}Hz · {{ dev.maxInputChannels }}ch</span>
        </div>
        <div class="adp-device-actions">
          <transition name="adp-fade">
            <span v-if="testResults[`mic-${dev.index}`]" class="adp-test-result">
              {{ testResults[`mic-${dev.index}`].icon }} {{ testResults[`mic-${dev.index}`].text }}
            </span>
          </transition>
          <span v-if="testingDevices[`mic-${dev.index}`]" class="adp-testing">测试中…</span>
          <span v-if="selectedMic === dev.index" class="adp-active-indicator" title="当前选中">✓</span>
          <button
            class="adp-btn-test"
            @click.stop="testDevice('mic', dev.index)"
            :disabled="testingDevices[`mic-${dev.index}`]"
            title="测试此设备"
          >测试</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.adp {
  border-bottom: 1px solid #e0e0e0;
  background: #fafafa;
  max-height: 420px;
  overflow-y: auto;
}

.adp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  border-bottom: 1px solid #eee;
  position: sticky;
  top: 0;
  background: #fafafa;
  z-index: 1;
}

.adp-title {
  font-size: 13px;
  font-weight: 600;
}

.adp-header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.adp-close {
  border: none;
  background: none;
  font-size: 16px;
  cursor: pointer;
  color: #999;
  padding: 2px 6px;
}

.adp-close:hover {
  color: #333;
}

.adp-btn-small {
  border: none;
  background: none;
  font-size: 14px;
  cursor: pointer;
  padding: 2px 4px;
  opacity: 0.6;
  transition: opacity 0.15s;
}

.adp-btn-small:hover {
  opacity: 1;
}

.adp-btn-small:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.adp-error {
  padding: 6px 12px;
  background: #ffebee;
  color: #c62828;
  font-size: 12px;
}

.adp-loading {
  padding: 12px;
  text-align: center;
  color: #999;
  font-size: 12px;
}

/* ── Section ── */

.adp-section {
  padding: 6px 12px;
  border-bottom: 1px solid #f0f0f0;
}

.adp-section:last-child {
  border-bottom: none;
}

.adp-section-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.adp-section-title {
  font-size: 11px;
  font-weight: 600;
  color: #666;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  flex-shrink: 0;
}

/* ── 内联实时电平（标题行右侧） ── */

.adp-inline-monitor {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
}

.adp-level-bar-container {
  flex: 0 0 70px;
  height: 6px;
  background: #eee;
  border-radius: 3px;
  overflow: hidden;
}

.adp-level-bar {
  height: 100%;
  border-radius: 3px;
  transition: width 0.15s ease-out, background 0.3s;
  min-width: 0;
}

.adp-wave {
  flex: 1;
  height: 22px;
  min-width: 40px;
  max-width: 120px;
}

.adp-monitor-placeholder {
  font-size: 10px;
  color: #bbb;
  font-style: italic;
}

/* ── 设备列表 ── */

.adp-empty {
  padding: 8px 0;
  color: #bbb;
  font-size: 12px;
  font-style: italic;
}

.adp-device {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 5px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.15s;
  margin-bottom: 2px;
}

.adp-device:hover {
  background: #e8eaf6;
}

.adp-device.active {
  background: #e3f2fd;
  border-left: 3px solid #1565c0;
  padding-left: 5px;
}

.adp-device-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.adp-device-name {
  font-size: 12px;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.adp-default-badge {
  display: inline-block;
  font-size: 9px;
  padding: 1px 4px;
  border-radius: 3px;
  background: #e8f5e9;
  color: #2e7d32;
  margin-left: 4px;
  vertical-align: middle;
}

.adp-device-meta {
  display: block;
  font-size: 10px;
  color: #999;
  margin-top: 1px;
}

.adp-device-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.adp-active-indicator {
  font-size: 14px;
  color: #1565c0;
  font-weight: 700;
}

.adp-btn-test {
  font-size: 10px;
  padding: 2px 8px;
  border: 1px solid #ddd;
  border-radius: 3px;
  background: #fff;
  color: #666;
  cursor: pointer;
  transition: all 0.15s;
}

.adp-btn-test:hover {
  background: #e3f2fd;
  border-color: #90caf9;
  color: #1565c0;
}

.adp-btn-test:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ── 内联测试结果 ── */

.adp-test-result {
  font-size: 10px;
  color: #333;
  white-space: nowrap;
  padding: 1px 6px;
  border-radius: 3px;
  background: #f5f5f5;
}

.adp-testing {
  font-size: 10px;
  color: #1565c0;
  white-space: nowrap;
  animation: adp-pulse 1s ease-in-out infinite;
}

@keyframes adp-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

/* ── 淡入淡出过渡 ── */

.adp-fade-enter-active {
  transition: opacity 0.2s ease;
}

.adp-fade-leave-active {
  transition: opacity 0.6s ease;
}

.adp-fade-enter-from,
.adp-fade-leave-to {
  opacity: 0;
}
</style>
