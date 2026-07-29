/**
 * 场景数据存储。
 *
 * 单一职责：管理闭麦表达助手场景的 CRUD 与持久化。
 * 场景独立于工作区（会议），所有会议共享同一份场景库。
 * 持久化路径：config/scenes.json（通过 Electron IPC）。
 */

import { reactive, computed } from 'vue';

/**
 * @typedef {Object} Scene
 * @property {string} id
 * @property {string} name
 * @property {string} description
 * @property {boolean} is_default
 */

const state = reactive({
  /** @type {Scene[]} */
  scenes: [],

  /** 当前选中的场景 ID */
  activeSceneId: '',

  /** 是否已加载 */
  loaded: false,
});

// ── 持久化 ──

async function load() {
  if (!window.electronAPI?.listScenes) return;
  try {
    const result = await window.electronAPI.listScenes();
    if (result.ok && Array.isArray(result.scenes)) {
      state.scenes = result.scenes;
      // 默认选中 is_default 场景或第一个
      const defaultScene = state.scenes.find((s) => s.is_default) || state.scenes[0];
      if (defaultScene && !state.activeSceneId) {
        state.activeSceneId = defaultScene.id;
      }
    }
  } catch (err) {
    console.error('[scene-store] Failed to load scenes:', err);
  }
  state.loaded = true;
}

async function _persist() {
  if (!window.electronAPI?.saveScenes) return;
  try {
    const plain = state.scenes.map((s) => ({
      id: s.id,
      name: s.name,
      description: s.description,
      is_default: s.is_default,
    }));
    await window.electronAPI.saveScenes(plain);
  } catch (err) {
    console.error('[scene-store] Failed to save scenes:', err);
  }
}

// ── CRUD ──

function _generateId() {
  return 'scene_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 6);
}

/**
 * 新增场景。
 * @param {string} name
 * @param {string} description
 * @returns {Scene}
 */
function addScene(name, description) {
  const scene = {
    id: _generateId(),
    name,
    description,
    is_default: false,
  };
  state.scenes.push(scene);
  _persist();
  return scene;
}

/**
 * 更新场景。
 * @param {string} id
 * @param {{ name?: string, description?: string }} patch
 */
function updateScene(id, patch) {
  const scene = state.scenes.find((s) => s.id === id);
  if (!scene) return;
  if (patch.name !== undefined) scene.name = patch.name;
  if (patch.description !== undefined) scene.description = patch.description;
  _persist();
}

/**
 * 删除场景（至少保留一个）。
 * @param {string} id
 * @returns {boolean} 是否成功删除
 */
function removeScene(id) {
  if (state.scenes.length <= 1) return false;
  const idx = state.scenes.findIndex((s) => s.id === id);
  if (idx === -1) return false;

  const wasActive = state.activeSceneId === id;
  const wasDefault = state.scenes[idx].is_default;
  state.scenes.splice(idx, 1);

  // 如果删掉的是当前选中的，切换到第一个
  if (wasActive) {
    state.activeSceneId = state.scenes[0]?.id || '';
  }
  // 如果删掉的是默认的，把第一个设为默认
  if (wasDefault && state.scenes.length > 0) {
    state.scenes[0].is_default = true;
  }

  _persist();
  return true;
}

/**
 * 设置当前活跃场景。
 * @param {string} id
 */
function setActive(id) {
  const scene = state.scenes.find((s) => s.id === id);
  if (scene) {
    state.activeSceneId = id;
  }
}

/**
 * 设置默认场景（持久化标记）。
 * @param {string} id
 */
function setDefault(id) {
  for (const s of state.scenes) {
    s.is_default = s.id === id;
  }
  _persist();
}

// ── 计算属性 ──

const activeScene = computed(() => {
  return state.scenes.find((s) => s.id === state.activeSceneId) || state.scenes[0] || null;
});

export const sceneStore = {
  state,
  activeScene,
  load,
  addScene,
  updateScene,
  removeScene,
  setActive,
  setDefault,
};
