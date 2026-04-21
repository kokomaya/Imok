<script setup>
/**
 * 可编辑列表字段组件。
 *
 * 单一职责：管理字符串数组的增删改，渲染每一项为 InlineEdit。
 * 通过 v-model:items 与父组件通信。
 */

import InlineEdit from './InlineEdit.vue';

const props = defineProps({
  items: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  addLabel: { type: String, default: '+ 添加' },
});

const emit = defineEmits(['update:items']);

function onItemEdit(index, newText) {
  if (!newText) {
    // 空文本视为删除
    removeItem(index);
    return;
  }
  const copy = [...props.items];
  copy[index] = newText;
  emit('update:items', copy);
}

function removeItem(index) {
  const copy = [...props.items];
  copy.splice(index, 1);
  emit('update:items', copy);
}

function addItem() {
  emit('update:items', [...props.items, '']);
}
</script>

<template>
  <div class="editable-field">
    <div
      v-for="(item, i) in items"
      :key="i"
      class="ef-item"
    >
      <button
        v-if="!disabled"
        class="ef-remove"
        @click="removeItem(i)"
        title="删除"
      >×</button>
      <InlineEdit
        :modelValue="item"
        :disabled="disabled"
        placeholder="点击编辑…"
        @update:modelValue="onItemEdit(i, $event)"
      />
    </div>
    <button
      v-if="!disabled"
      class="ef-add"
      @click="addItem"
    >{{ addLabel }}</button>
  </div>
</template>

<style scoped>
.editable-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ef-item {
  display: flex;
  align-items: center;
  gap: 4px;
}
.ef-remove {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  padding: 0;
  font-size: 14px;
  line-height: 1;
  color: var(--text-secondary, #999);
  background: none;
  border: none;
  border-radius: 3px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, color 0.15s;
}
.ef-item:hover .ef-remove {
  opacity: 1;
}
.ef-remove:hover {
  color: #ef5350;
}
.ef-add {
  align-self: flex-start;
  padding: 2px 8px;
  font-size: 12px;
  color: var(--accent, #64b5f6);
  background: none;
  border: 1px dashed var(--border-color, rgba(255, 255, 255, 0.15));
  border-radius: 3px;
  cursor: pointer;
  opacity: 0.7;
  transition: opacity 0.15s;
}
.ef-add:hover {
  opacity: 1;
}
</style>
