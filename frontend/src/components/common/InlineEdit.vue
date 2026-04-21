<script setup>
/**
 * 通用内联编辑组件。
 *
 * 单一职责：管理只读/编辑态切换、键盘交互、提交/取消逻辑。
 * 不包含业务逻辑，通过 v-model 与父组件通信。
 */

import { ref, nextTick, watch } from 'vue';

const props = defineProps({
  modelValue: { type: String, default: '' },
  tag: { type: String, default: 'span' },
  multiline: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '点击编辑…' },
});

const emit = defineEmits(['update:modelValue']);

const editing = ref(false);
const draft = ref('');
const inputRef = ref(null);

function startEdit() {
  if (props.disabled) return;
  draft.value = props.modelValue;
  editing.value = true;
  nextTick(() => {
    const el = inputRef.value;
    if (el) {
      el.focus();
      el.select?.();
    }
  });
}

function commit() {
  editing.value = false;
  const trimmed = draft.value.trim();
  if (trimmed !== props.modelValue) {
    emit('update:modelValue', trimmed);
  }
}

function cancel() {
  editing.value = false;
  draft.value = props.modelValue;
}

function onKeydown(e) {
  if (e.key === 'Escape') {
    cancel();
    return;
  }
  if (props.multiline) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      commit();
    }
  } else {
    if (e.key === 'Enter') {
      commit();
    }
  }
}

watch(() => props.modelValue, (v) => {
  if (!editing.value) draft.value = v;
});
</script>

<template>
  <!-- 编辑态 -->
  <textarea
    v-if="editing && multiline"
    ref="inputRef"
    class="ile-input ile-textarea"
    v-model="draft"
    @blur="commit"
    @keydown="onKeydown"
  />
  <input
    v-else-if="editing"
    ref="inputRef"
    class="ile-input"
    type="text"
    v-model="draft"
    @blur="commit"
    @keydown="onKeydown"
  />
  <!-- 只读态 -->
  <component
    v-else
    :is="tag"
    class="ile-display"
    :class="{ editable: !disabled, empty: !modelValue }"
    @dblclick="startEdit"
  >{{ modelValue || placeholder }}</component>
</template>

<style scoped>
.ile-display {
  cursor: default;
  border-radius: 3px;
  transition: background 0.15s;
}
.ile-display.editable {
  cursor: text;
  border-bottom: 1px dashed transparent;
}
.ile-display.editable:hover {
  background: var(--bg-secondary, rgba(255, 255, 255, 0.06));
  border-bottom-color: var(--border-color, rgba(255, 255, 255, 0.2));
}
.ile-display.empty {
  opacity: 0.4;
  font-style: italic;
}
.ile-input {
  width: 100%;
  padding: 2px 4px;
  font: inherit;
  color: inherit;
  background: var(--bg-secondary, rgba(255, 255, 255, 0.08));
  border: 1px solid var(--border-color, rgba(255, 255, 255, 0.2));
  border-radius: 3px;
  outline: none;
  box-sizing: border-box;
}
.ile-input:focus {
  border-color: var(--accent, #64b5f6);
}
.ile-textarea {
  min-height: 60px;
  resize: vertical;
}
</style>
