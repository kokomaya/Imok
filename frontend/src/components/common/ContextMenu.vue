<script setup>
/**
 * 通用右键上下文菜单。
 *
 * 单一职责：在指定坐标渲染一个菜单，点击项发出 select 事件。
 * 不含业务逻辑，菜单项与行为由父组件通过 props/事件决定。
 */

import { ref, watch, onBeforeUnmount, nextTick } from 'vue';

const props = defineProps({
  visible: { type: Boolean, default: false },
  x: { type: Number, default: 0 },
  y: { type: Number, default: 0 },
  /** @type {{ key: string, label: string, icon?: string, disabled?: boolean }[]} */
  items: { type: Array, default: () => [] },
});

const emit = defineEmits(['select', 'close']);

const menuRef = ref(null);
const pos = ref({ x: 0, y: 0 });

// 根据视口边界修正菜单位置，避免溢出
watch(
  () => props.visible,
  async (v) => {
    if (!v) return;
    pos.value = { x: props.x, y: props.y };
    await nextTick();
    const el = menuRef.value;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    let { x, y } = props;
    if (x + rect.width > window.innerWidth) x = window.innerWidth - rect.width - 8;
    if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 8;
    pos.value = { x: Math.max(4, x), y: Math.max(4, y) };
  },
);

function onSelect(item) {
  if (item.disabled) return;
  emit('select', item.key);
  emit('close');
}

function onGlobalPointer(e) {
  if (!props.visible) return;
  if (menuRef.value && menuRef.value.contains(e.target)) return;
  emit('close');
}

function onKeydown(e) {
  if (props.visible && e.key === 'Escape') emit('close');
}

watch(
  () => props.visible,
  (v) => {
    if (v) {
      document.addEventListener('pointerdown', onGlobalPointer, true);
      document.addEventListener('keydown', onKeydown, true);
      window.addEventListener('blur', () => emit('close'), { once: true });
    } else {
      document.removeEventListener('pointerdown', onGlobalPointer, true);
      document.removeEventListener('keydown', onKeydown, true);
    }
  },
);

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onGlobalPointer, true);
  document.removeEventListener('keydown', onKeydown, true);
});
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible"
      ref="menuRef"
      class="context-menu"
      :style="{ left: pos.x + 'px', top: pos.y + 'px' }"
    >
      <button
        v-for="item in items"
        :key="item.key"
        class="context-menu-item"
        :class="{ disabled: item.disabled }"
        :disabled="item.disabled"
        @click="onSelect(item)"
      >
        <span v-if="item.icon" class="context-menu-icon">{{ item.icon }}</span>
        <span class="context-menu-label">{{ item.label }}</span>
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
.context-menu {
  position: fixed;
  z-index: 10000;
  min-width: 140px;
  padding: 4px;
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
  user-select: none;
}

.context-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  background: transparent;
  font-size: 13px;
  color: #333;
  text-align: left;
  border-radius: 4px;
  cursor: pointer;
}

.context-menu-item:hover:not(.disabled) {
  background: #e3f2fd;
}

.context-menu-item.disabled {
  color: #bbb;
  cursor: not-allowed;
}

.context-menu-icon {
  width: 16px;
  text-align: center;
  flex-shrink: 0;
}
</style>
