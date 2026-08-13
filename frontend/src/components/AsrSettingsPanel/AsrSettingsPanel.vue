<script setup>
import { computed } from 'vue';
import { asrSettingsStore } from '@/stores/asr-settings-store.js';

defineProps({
  visible: { type: Boolean, default: false },
});
defineEmits(['close']);

const state = asrSettingsStore.state;
const runtime = asrSettingsStore.runtime;
const RANGES = asrSettingsStore.RANGES;
const OPTIONS = asrSettingsStore.OPTIONS;

const activePreset = computed(() => asrSettingsStore.matchedPreset());

const PRESET_META = [
  { key: 'lowLatency', label: '低延迟', hint: '字幕最快，精度略降' },
  { key: 'balanced', label: '均衡', hint: '当前默认参数' },
  { key: 'highAccuracy', label: '高精度', hint: '更准，延迟略高' },
];

const LANG_LABELS = {
  auto: '自动检测', zh: '中文', en: 'English', ja: '日本語', ko: '한국어', yue: '粤语',
};

function optionLabel(key, val) {
  if (key === 'language') return LANG_LABELS[val] || val;
  return val === 'auto' ? '自动' : val;
}

function onNumber(key, e) {
  asrSettingsStore.set(key, e.target.value);
}
function onSelect(key, e) {
  asrSettingsStore.set(key, e.target.value);
}
function onToggle(key, e) {
  asrSettingsStore.set(key, e.target.checked);
}
function applyPreset(key) {
  asrSettingsStore.applyPreset(key);
}
function restoreDefaults() {
  asrSettingsStore.restoreDefaults();
}
</script>

<template>
  <div v-if="visible" class="asp">
    <div class="asp-header">
      <span class="asp-title">🎚 语音识别设置</span>
      <div class="asp-header-actions">
        <button class="asp-btn-reset" @click="restoreDefaults" title="恢复到当前默认参数">↺ 恢复默认</button>
        <button class="asp-close" @click="$emit('close')">✕</button>
      </div>
    </div>

    <!-- 性能预设 -->
    <div class="asp-section">
      <div class="asp-section-title">性能预设</div>
      <div class="asp-presets">
        <button
          v-for="p in PRESET_META"
          :key="p.key"
          class="asp-preset"
          :class="{ active: activePreset === p.key }"
          @click="applyPreset(p.key)"
          :title="p.hint"
        >
          <span class="asp-preset-label">{{ p.label }}</span>
          <span class="asp-preset-hint">{{ p.hint }}</span>
        </button>
      </div>
      <div v-if="!activePreset" class="asp-preset-custom">自定义</div>
    </div>

    <!-- 识别延迟 (VAD) -->
    <div class="asp-section">
      <div class="asp-section-title">识别延迟（VAD）</div>

      <div class="asp-field">
        <div class="asp-field-head">
          <label>最大语音段时长</label>
          <span class="asp-value">{{ state.vad_max_segment_s }} s</span>
        </div>
        <input
          type="range"
          :min="RANGES.vad_max_segment_s.min" :max="RANGES.vad_max_segment_s.max"
          :step="RANGES.vad_max_segment_s.step" :value="state.vad_max_segment_s"
          @input="onNumber('vad_max_segment_s', $event)"
        />
        <div class="asp-desc">🚀 调小可让连续说话时字幕更快出现（延迟头号因素）</div>
      </div>

      <div class="asp-field">
        <div class="asp-field-head">
          <label>句末静音判定</label>
          <span class="asp-value">{{ state.vad_min_silence_ms }} ms</span>
        </div>
        <input
          type="range"
          :min="RANGES.vad_min_silence_ms.min" :max="RANGES.vad_min_silence_ms.max"
          :step="RANGES.vad_min_silence_ms.step" :value="state.vad_min_silence_ms"
          @input="onNumber('vad_min_silence_ms', $event)"
        />
        <div class="asp-desc">🚀 调小可减少每句尾部延迟；过小可能把一句话切碎</div>
      </div>

      <div class="asp-field">
        <div class="asp-field-head">
          <label>VAD 灵敏度阈值</label>
          <span class="asp-value">{{ state.vad_threshold }}</span>
        </div>
        <input
          type="range"
          :min="RANGES.vad_threshold.min" :max="RANGES.vad_threshold.max"
          :step="RANGES.vad_threshold.step" :value="state.vad_threshold"
          @input="onNumber('vad_threshold', $event)"
        />
        <div class="asp-desc">越高越不容易误判噪声为语音；越低越灵敏</div>
      </div>
    </div>

    <!-- 识别引擎 -->
    <div class="asp-section">
      <div class="asp-section-title">识别引擎</div>

      <div class="asp-field">
        <div class="asp-field-head">
          <label>模型大小</label>
        </div>
        <select :value="state.model_size" @change="onSelect('model_size', $event)">
          <option v-for="o in OPTIONS.model_size" :key="o" :value="o">{{ optionLabel('model_size', o) }}</option>
        </select>
        <div class="asp-desc">模型越大越准但越慢；small 比 medium 快约 2 倍。改动将在下次开始识别后生效</div>
      </div>

      <div class="asp-field">
        <div class="asp-field-head">
          <label>计算精度</label>
        </div>
        <select :value="state.compute_type" @change="onSelect('compute_type', $event)">
          <option v-for="o in OPTIONS.compute_type" :key="o" :value="o">{{ optionLabel('compute_type', o) }}</option>
        </select>
        <div class="asp-desc">int8 更快省显存；float16 更准（需 GPU）</div>
      </div>

      <div class="asp-field">
        <div class="asp-field-head">
          <label>推理设备</label>
        </div>
        <select :value="state.device" @change="onSelect('device', $event)">
          <option v-for="o in OPTIONS.device" :key="o" :value="o">{{ optionLabel('device', o) }}</option>
        </select>
        <div class="asp-desc">auto 会优先使用 GPU（若可用），否则回落 CPU</div>
      </div>

      <div class="asp-field">
        <div class="asp-field-head">
          <label>识别语言</label>
        </div>
        <select :value="state.language" @change="onSelect('language', $event)">
          <option v-for="o in OPTIONS.language" :key="o" :value="o">{{ optionLabel('language', o) }}</option>
        </select>
        <div class="asp-desc">固定语言可省去自动检测开销；auto 支持中英混说</div>
      </div>

      <div class="asp-field">
        <div class="asp-field-head">
          <label>解码 Beam 宽度</label>
          <span class="asp-value">{{ state.beam_size }}</span>
        </div>
        <input
          type="range"
          :min="RANGES.beam_size.min" :max="RANGES.beam_size.max"
          :step="RANGES.beam_size.step" :value="state.beam_size"
          @input="onNumber('beam_size', $event)"
        />
        <div class="asp-desc">1 = 贪心解码（最快）；越大越准但越慢</div>
      </div>

      <div class="asp-field asp-field-row">
        <label class="asp-switch">
          <input type="checkbox" :checked="state.word_timestamps" @change="onToggle('word_timestamps', $event)" />
          <span>词级时间戳</span>
        </label>
        <div class="asp-desc">🚀 关闭可提速约 10~25%，字幕内容不受影响</div>
      </div>
    </div>

    <div class="asp-footer">
      <span class="asp-note">⚙ 大部分设置将在<b>下次开始识别</b>后生效</span>
      <span v-if="runtime.effective" class="asp-effective">当前生效：{{ runtime.effective }}</span>
    </div>
  </div>
</template>

<style scoped src="./AsrSettingsPanel.scoped.css"></style>
