/**
 * 帮助弹窗状态存储。
 *
 * 单一职责：管理帮助弹窗的可见性和当前活动 tab。
 */

import { reactive } from 'vue';

const state = reactive({
  visible: false,
  activeTab: 'about', // 'about' | 'shortcuts' | 'doc'
});

function open(tab = 'about') {
  state.activeTab = tab;
  state.visible = true;
}

function close() {
  state.visible = false;
}

function setTab(tab) {
  state.activeTab = tab;
}

export const helpStore = { state, open, close, setTab };
