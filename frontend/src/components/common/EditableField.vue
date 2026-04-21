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

<style scoped src="./EditableField.scoped.css"></style>
