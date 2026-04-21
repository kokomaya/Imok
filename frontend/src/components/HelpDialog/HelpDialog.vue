<template>
  <Teleport to="body">
    <div v-if="helpStore.state.visible" class="help-overlay" @click.self="helpStore.close()">
      <div class="help-dialog">
        <header class="help-dialog-header">
          <nav class="help-tabs">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              class="help-tab"
              :class="{ active: helpStore.state.activeTab === tab.key }"
              @click="helpStore.setTab(tab.key)"
            >{{ tab.label }}</button>
          </nav>
          <button class="help-close" @click="helpStore.close()" title="关闭">✕</button>
        </header>
        <div class="help-dialog-body">
          <component :is="activeComponent" />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed } from 'vue';
import { helpStore } from '@/stores/help-store.js';
import AboutContent from './AboutContent.vue';
import ShortcutsContent from './ShortcutsContent.vue';
import HelpDocContent from './HelpDocContent.vue';

const tabs = [
  { key: 'about', label: '关于' },
  { key: 'shortcuts', label: '快捷键' },
  { key: 'doc', label: '使用帮助' },
];

const componentMap = {
  about: AboutContent,
  shortcuts: ShortcutsContent,
  doc: HelpDocContent,
};

const activeComponent = computed(() => componentMap[helpStore.state.activeTab] || AboutContent);
</script>

<style scoped src="./HelpDialog.scoped.css"></style>
