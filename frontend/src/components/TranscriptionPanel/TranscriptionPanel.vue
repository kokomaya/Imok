<script setup>
/**
 * 实时字幕面板。
 *
 * 单一职责：展示字幕条目并提供编辑、导出、右键菜单、翻译/解释注解交互。
 * 逻辑委托给 useTranscriptionActions；数据由父组件通过 props 传入。
 */

import { ref, toRef, watch } from 'vue';
import ContextMenu from '@/components/common/ContextMenu.vue';
import { useTranscriptionActions } from '@/composables/useTranscriptionActions.js';

const props = defineProps({
  transcriptions: { type: Array, default: () => [] },
  meetingActive: { type: Boolean, default: false },
});

const emit = defineEmits(['clear']);

const {
  listRef,
  editingId,
  ctxMenu,
  scrollToBottom,
  startEdit,
  commitEdit,
  onExport,
  openContextMenu,
  closeContextMenu,
  onMenuSelect,
  toggleAnnotation,
} = useTranscriptionActions(toRef(props, 'transcriptions'));

// 新字幕到达时自动滚动到底部
watch(
  () => props.transcriptions,
  () => scrollToBottom(),
  { deep: true },
);

// 清空需二次确认，避免误触丢失字幕
const confirmingClear = ref(false);

function requestClear() {
  confirmingClear.value = true;
}

function cancelClear() {
  confirmingClear.value = false;
}

function confirmClear() {
  confirmingClear.value = false;
  emit('clear');
}

// 字幕清空后复位确认态
watch(
  () => props.transcriptions.length,
  (len) => {
    if (len === 0) confirmingClear.value = false;
  },
);
</script>

<template>
  <section class="transcription-panel">
    <div class="transcription-header">
      <h2>实时字幕</h2>
      <div class="transcription-actions">
        <div v-if="transcriptions.length > 0" class="export-menu">
          <button class="btn-export" title="导出字幕文件">⬇ 导出</button>
          <div class="export-dropdown">
            <button @click="onExport('srt')">SRT (.srt)</button>
            <button @click="onExport('vtt')">WebVTT (.vtt)</button>
          </div>
        </div>
        <template v-if="transcriptions.length > 0">
          <button
            v-if="!confirmingClear"
            class="btn-clear-transcriptions"
            @click="requestClear"
            title="清空当前字幕"
          >
            🗑 清空
          </button>
          <div v-else class="clear-confirm">
            <span class="clear-confirm-text">确认清空？</span>
            <button class="btn-clear-confirm" @click="confirmClear" title="确认清空">
              确认
            </button>
            <button class="btn-clear-cancel" @click="cancelClear" title="取消">
              取消
            </button>
          </div>
        </template>
      </div>
    </div>

    <div
      class="transcription-list"
      ref="listRef"
      @contextmenu="openContextMenu($event, null)"
    >
      <p v-if="transcriptions.length === 0" class="placeholder">
        {{ meetingActive ? '等待语音输入…' : '点击「开始会议」启动录制' }}
      </p>
      <div
        v-for="item in transcriptions"
        :key="item.id"
        class="transcription-item"
        @contextmenu.stop="openContextMenu($event, item)"
      >
        <span class="time">{{ item.timestamp }}</span>
        <span class="source-icon" v-if="item.source" :title="item.source === 'mic' ? '麦克风' : '系统音频'">{{ item.source === 'mic' ? '🎤' : '🔊' }}</span>
        <span class="speaker" v-if="item.speaker">[{{ item.speaker }}]</span>
        <span class="lang" v-if="item.language">[{{ item.language }}]</span>
        <span class="text-wrap">
          <span
            class="text"
            :data-edit-id="item.id"
            :contenteditable="editingId === item.id"
            :class="{ editing: editingId === item.id }"
            @dblclick="startEdit(item)"
            @blur="commitEdit(item, $event)"
            @keydown.enter.prevent="$event.target.blur()"
          >{{ item.text }}</span>
          <template v-for="ann in (item.annotations || [])" :key="ann.id">
            <button
              class="annotation-chip"
              :class="ann.type"
              :title="ann.type === 'translate' ? '翻译' : '解释'"
              @click="toggleAnnotation(ann)"
            >{{ ann.type === 'translate' ? '🌐' : '💡' }}</button>
            <span v-if="ann.open" class="annotation-popover" :class="ann.type">
              <span class="annotation-label">{{ ann.type === 'translate' ? '翻译' : '解释' }}</span>
              <span v-if="ann.loading" class="annotation-text loading">生成中…</span>
              <span v-else class="annotation-text">{{ ann.text }}</span>
            </span>
          </template>
        </span>
      </div>
    </div>

    <ContextMenu
      :visible="ctxMenu.visible"
      :x="ctxMenu.x"
      :y="ctxMenu.y"
      :items="ctxMenu.items"
      @select="onMenuSelect"
      @close="closeContextMenu"
    />
  </section>
</template>

<style scoped src="./TranscriptionPanel.scoped.css"></style>
