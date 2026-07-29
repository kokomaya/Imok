<script setup>
/**
 * 场景管理弹窗。
 *
 * 单一职责：场景的新增、编辑、删除 UI。
 * 数据来源：sceneStore。
 */

import { ref, computed } from 'vue';
import { sceneStore } from '@/stores/scene-store.js';
import { expressionSettingsStore } from '@/stores/expression-settings-store.js';

const emit = defineEmits(['close']);

// ── 编辑状态 ──

const editingId = ref(null);
const editName = ref('');
const editDesc = ref('');

// ── 新增状态 ──

const adding = ref(false);
const newName = ref('');
const newDesc = ref('');

// ── 计算属性 ──

const scenes = computed(() => sceneStore.state.scenes);
const candidateCount = computed(() => expressionSettingsStore.state.candidateCount);

// ── 新增场景 ──

function startAdd() {
  adding.value = true;
  newName.value = '';
  newDesc.value = '';
  cancelEdit();
}

function confirmAdd() {
  const name = newName.value.trim();
  const desc = newDesc.value.trim();
  if (!name) return;

  sceneStore.addScene(name, desc || name);
  adding.value = false;
  newName.value = '';
  newDesc.value = '';
}

function cancelAdd() {
  adding.value = false;
}

// ── 编辑场景 ──

function startEdit(scene) {
  editingId.value = scene.id;
  editName.value = scene.name;
  editDesc.value = scene.description;
  adding.value = false;
}

function confirmEdit() {
  if (!editingId.value) return;
  const name = editName.value.trim();
  if (!name) return;

  sceneStore.updateScene(editingId.value, {
    name,
    description: editDesc.value.trim() || name,
  });
  editingId.value = null;
}

function cancelEdit() {
  editingId.value = null;
}

// ── 删除场景 ──

function handleDelete(id) {
  if (scenes.value.length <= 1) {
    window.alert('至少保留一个场景');
    return;
  }
  if (!window.confirm('确定删除该场景？')) return;
  sceneStore.removeScene(id);
}

// ── 候选条数 ──

function onCandidateChange(e) {
  expressionSettingsStore.setCandidateCount(Number(e.target.value));
}
</script>

<template>
  <div class="scene-manager">
    <div class="sm-header">
      <span class="sm-title">场景管理</span>
      <button class="sm-close" @click="emit('close')">✕</button>
    </div>

    <!-- 候选条数设置 -->
    <div class="sm-setting-row">
      <label class="sm-setting-label">候选表达条数</label>
      <select class="sm-select" :value="candidateCount" @change="onCandidateChange">
        <option v-for="n in 5" :key="n" :value="n">{{ n }} 条</option>
      </select>
    </div>

    <div class="sm-divider"></div>

    <!-- 场景列表 -->
    <div class="sm-list">
      <div
        v-for="scene in scenes"
        :key="scene.id"
        class="sm-item"
        :class="{ editing: editingId === scene.id }"
      >
        <!-- 查看模式 -->
        <template v-if="editingId !== scene.id">
          <div class="sm-item-body">
            <div class="sm-item-name">
              {{ scene.name }}
              <span v-if="scene.is_default" class="sm-default-badge">默认</span>
            </div>
            <div class="sm-item-desc">{{ scene.description }}</div>
          </div>
          <div class="sm-item-actions">
            <button class="sm-btn sm-btn-edit" @click="startEdit(scene)" title="编辑">✏️</button>
            <button
              class="sm-btn sm-btn-delete"
              @click="handleDelete(scene.id)"
              :disabled="scenes.length <= 1"
              title="删除"
            >🗑</button>
          </div>
        </template>

        <!-- 编辑模式 -->
        <template v-else>
          <div class="sm-edit-form">
            <input
              v-model="editName"
              class="sm-input"
              placeholder="场景名称"
              @keydown.enter="confirmEdit"
            />
            <textarea
              v-model="editDesc"
              class="sm-textarea"
              placeholder="场景描述（LLM 根据此描述调整语气和措辞）"
              rows="3"
            ></textarea>
            <div class="sm-edit-actions">
              <button class="sm-btn sm-btn-confirm" @click="confirmEdit">保存</button>
              <button class="sm-btn sm-btn-cancel" @click="cancelEdit">取消</button>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- 新增场景 -->
    <div v-if="adding" class="sm-add-form">
      <input
        v-model="newName"
        class="sm-input"
        placeholder="场景名称（如：与客户交流）"
        @keydown.enter="confirmAdd"
      />
      <textarea
        v-model="newDesc"
        class="sm-textarea"
        placeholder="场景描述（LLM 根据此描述调整语气和措辞）"
        rows="3"
      ></textarea>
      <div class="sm-edit-actions">
        <button class="sm-btn sm-btn-confirm" @click="confirmAdd" :disabled="!newName.trim()">添加</button>
        <button class="sm-btn sm-btn-cancel" @click="cancelAdd">取消</button>
      </div>
    </div>

    <button v-if="!adding" class="sm-btn sm-btn-add" @click="startAdd">+ 新增场景</button>
  </div>
</template>

<style scoped src="./SceneManager.scoped.css"></style>
