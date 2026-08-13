/**
 * 语音识别（ASR / VAD）参数设置存储。
 *
 * 单一职责：管理识别速度/精度相关参数，持久化到 config/asr_settings.json，
 * 并通过 IPC `set_asr_config` 下发给后端（下次开始识别时生效）。
 *
 * 注意：DEFAULTS 必须与后端 backend/config.py::ASRSettings 的字段默认值保持一致，
 * “恢复默认” / “均衡”预设都以此为基准，即用户当前使用的这套（准确的）参数。
 */

import { reactive } from 'vue';

// 当前参数（= 默认值 = “均衡”预设 = 后端 ASRSettings 默认值）
export const DEFAULTS = {
  // VAD —— 影响“从说话到字幕”的延迟
  vad_max_segment_s: 15.0,   // 最大语音段时长(s)，连续说话时字幕延迟头号因素
  vad_min_silence_ms: 300,   // 判定句子结束的静音阈值(ms)
  vad_threshold: 0.5,        // VAD 置信度阈值
  // ASR 引擎
  beam_size: 1,              // 1 = 贪心解码（最快）
  model_size: 'auto',        // auto → 按 GPU 自动选择
  compute_type: 'auto',      // auto/int8/int8_float16/float16
  device: 'auto',            // auto/cuda/cpu
  language: 'auto',          // auto/zh/en/ja/...
  word_timestamps: true,     // 词级时间戳；下游未使用，关闭可提速(~10-25%)
};

// 性能预设。“均衡” = 当前参数（DEFAULTS）。
export const PRESETS = {
  // 低延迟：更短的段 + 更短静音 + 关闭词级时间戳，字幕更快出现
  lowLatency: {
    ...DEFAULTS,
    vad_max_segment_s: 5.0,
    vad_min_silence_ms: 200,
    beam_size: 1,
    word_timestamps: false,
  },
  // 均衡（= 当前，准确）
  balanced: { ...DEFAULTS },
  // 高精度：更大 beam + 更长静音（少切句）+ 保留词级时间戳
  highAccuracy: {
    ...DEFAULTS,
    vad_max_segment_s: 15.0,
    vad_min_silence_ms: 400,
    beam_size: 5,
    word_timestamps: true,
  },
};

// 各参数取值范围（与后端钳制范围一致），用于 UI 与校验
export const RANGES = {
  vad_max_segment_s: { min: 3, max: 30, step: 0.5 },
  vad_min_silence_ms: { min: 100, max: 2000, step: 50 },
  vad_threshold: { min: 0.1, max: 0.9, step: 0.05 },
  beam_size: { min: 1, max: 10, step: 1 },
};

export const OPTIONS = {
  model_size: ['auto', 'tiny', 'base', 'small', 'medium', 'large-v2', 'large-v3'],
  compute_type: ['auto', 'int8', 'int8_float16', 'float16', 'float32'],
  device: ['auto', 'cuda', 'cpu'],
  language: ['auto', 'zh', 'en', 'ja', 'ko', 'yue'],
};

const state = reactive({ ...DEFAULTS });

/** 后端上次启动实际生效的模型/设备（来自 status 消息 asr_model），仅展示用。 */
const runtime = reactive({ effective: '' });

function _clampNumber(key, val) {
  const r = RANGES[key];
  const n = Number(val);
  if (Number.isNaN(n)) return DEFAULTS[key];
  if (!r) return n;
  return Math.max(r.min, Math.min(r.max, n));
}

/** 归一化任意来源（磁盘/预设）的部分设置为合法完整状态。 */
function _normalize(partial) {
  const out = { ...DEFAULTS };
  if (!partial || typeof partial !== 'object') return out;
  for (const key of Object.keys(DEFAULTS)) {
    if (partial[key] === undefined || partial[key] === null) continue;
    if (key in RANGES) {
      out[key] = _clampNumber(key, partial[key]);
    } else if (key === 'word_timestamps') {
      out[key] = !!partial[key];
    } else if (OPTIONS[key]) {
      out[key] = OPTIONS[key].includes(partial[key]) ? partial[key] : DEFAULTS[key];
    }
  }
  return out;
}

function _snapshot() {
  const s = {};
  for (const key of Object.keys(DEFAULTS)) s[key] = state[key];
  return s;
}

/** 下发到后端子进程（下次开始识别时生效）。 */
function _sendToBackend() {
  window.electronAPI?.sendControl?.('set_asr_config', _snapshot());
}

/** 持久化到 config/asr_settings.json。 */
async function _persist() {
  if (!window.electronAPI?.saveAsrSettings) return;
  try {
    await window.electronAPI.saveAsrSettings(_snapshot());
  } catch (err) {
    console.error('[asr-settings-store] Failed to save:', err);
  }
}

/** 启动时读取持久化设置并下发后端。 */
async function load() {
  if (window.electronAPI?.getAsrSettings) {
    try {
      const result = await window.electronAPI.getAsrSettings();
      if (result?.ok) {
        Object.assign(state, _normalize(result.settings));
      }
    } catch (err) {
      console.error('[asr-settings-store] Failed to load:', err);
    }
  }
  // 无论是否有持久化，都把当前状态下发一次，确保后端与 UI 一致
  _sendToBackend();
}

/** 设置单个参数（校验 + 持久化 + 下发）。 */
function set(key, val) {
  if (!(key in DEFAULTS)) return;
  if (key in RANGES) {
    state[key] = _clampNumber(key, val);
  } else if (key === 'word_timestamps') {
    state[key] = !!val;
  } else if (OPTIONS[key]) {
    state[key] = OPTIONS[key].includes(val) ? val : DEFAULTS[key];
  }
  _persist();
  _sendToBackend();
}

/** 应用预设（'lowLatency' | 'balanced' | 'highAccuracy'）。 */
function applyPreset(name) {
  const preset = PRESETS[name];
  if (!preset) return;
  Object.assign(state, _normalize(preset));
  _persist();
  _sendToBackend();
}

/** 恢复默认（= 当前这套准确参数 = 均衡预设）。 */
function restoreDefaults() {
  applyPreset('balanced');
}

/** 判断当前状态匹配哪个预设（用于高亮预设按钮），无匹配返回 ''。 */
function matchedPreset() {
  for (const [name, preset] of Object.entries(PRESETS)) {
    const norm = _normalize(preset);
    if (Object.keys(DEFAULTS).every((k) => norm[k] === state[k])) return name;
  }
  return '';
}

/** 记录后端上报的实际生效模型/设备（供面板展示）。 */
function setEffective(text) {
  runtime.effective = text || '';
}

export const asrSettingsStore = {
  state,
  runtime,
  DEFAULTS,
  PRESETS,
  RANGES,
  OPTIONS,
  load,
  set,
  applyPreset,
  restoreDefaults,
  matchedPreset,
  setEffective,
};
