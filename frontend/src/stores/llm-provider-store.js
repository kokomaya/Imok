/**
 * LLM 提供商切换存储。
 *
 * 单一职责：读取 llm_providers.yaml 中的可选提供商，管理当前选择，
 * 通过 IPC 写入 config/llm_active.json。切换对回看/降级立即生效，
 * 对后端实时管线在下次开始会议时生效。
 */

import { reactive, computed } from 'vue';

const state = reactive({
  /** @type {{ name: string, model: string, baseUrl: string, isLocal: boolean }[]} */
  providers: [],
  /** 当前生效的 provider 名称 */
  active: '',
  /** yaml 中的默认 provider 名称 */
  defaultProvider: '',
  loaded: false,
  error: '',
});

const hasLocal = computed(() => state.providers.some((p) => p.isLocal));

/** 加载可选提供商列表与当前选择。 */
async function load() {
  if (!window.electronAPI?.listLLMProviders) {
    state.loaded = true;
    return;
  }
  try {
    const result = await window.electronAPI.listLLMProviders();
    if (result?.ok) {
      state.providers = Array.isArray(result.providers) ? result.providers : [];
      state.active = result.active || '';
      state.defaultProvider = result.defaultProvider || '';
      state.error = '';
    } else {
      state.error = result?.error || '读取提供商列表失败';
    }
  } catch (err) {
    state.error = err.message;
  }
  state.loaded = true;
}

/** 切换当前 provider。 */
async function select(name) {
  if (!name || name === state.active) return;
  if (!state.providers.some((p) => p.name === name)) return;
  if (!window.electronAPI?.setLLMProvider) return;
  try {
    const result = await window.electronAPI.setLLMProvider(name);
    if (result?.ok) {
      state.active = name;
      state.error = '';
    } else {
      state.error = result?.error || '切换失败';
    }
  } catch (err) {
    state.error = err.message;
  }
}

export const llmProviderStore = {
  state,
  hasLocal,
  load,
  select,
};
