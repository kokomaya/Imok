/**
 * 实时字幕面板交互逻辑。
 *
 * 单一职责：封装字幕条目的编辑、导出、右键菜单与翻译/解释注解逻辑，
 * 与视图（TranscriptionPanel.vue）解耦。不直接操作 DOM 之外的业务 store。
 */

import { ref, reactive, nextTick } from 'vue';
import { workspaceStore } from '@/stores/workspace-store.js';
import { notificationStore } from '@/stores/notification-store.js';
import { exportSubtitles } from '@/services/subtitle-export.js';
import { translateText, explainText } from '@/services/text-actions.js';

/**
 * @param {import('vue').Ref<Array>} transcriptions 字幕条目响应式数组
 */
export function useTranscriptionActions(transcriptions) {
  const listRef = ref(null);
  const editingId = ref(null);
  const ctxMenu = ref({ visible: false, x: 0, y: 0, items: [], text: '', item: null });

  /** 滚动到底部（新字幕到达时调用）。 */
  function scrollToBottom() {
    nextTick(() => {
      const el = listRef.value;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }

  // ── 编辑：默认不可编辑（保证跨行选中），双击进入 ──

  function startEdit(item) {
    editingId.value = item.id;
    nextTick(() => {
      const el = listRef.value?.querySelector(`[data-edit-id="${item.id}"]`);
      if (!el) return;
      el.focus();
      const range = document.createRange();
      range.selectNodeContents(el);
      range.collapse(false);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    });
  }

  function commitEdit(item, event) {
    const newText = event.target.textContent.trim();
    if (newText !== item.text) {
      item.text = newText;
      workspaceStore.markTranscriptionEdited();
    }
    editingId.value = null;
  }

  // ── 导出（标准 SRT / VTT，仅原文）──

  function onExport(format) {
    const ok = exportSubtitles(transcriptions.value, format, 'imok-subtitles');
    if (!ok) notificationStore.notifyError('没有可导出的字幕');
  }

  // ── 右键菜单 ──

  function closeContextMenu() {
    ctxMenu.value.visible = false;
  }

  function openContextMenu(event, item) {
    if (editingId.value != null) return; // 编辑态用浏览器原生菜单
    event.preventDefault();

    const selectedText = (window.getSelection?.().toString() || '').trim();
    const targetText = selectedText || (item?.text || '').trim();
    const hasText = !!targetText;

    ctxMenu.value = {
      visible: true,
      x: event.clientX,
      y: event.clientY,
      text: targetText,
      item: item || null,
      items: [
        { key: 'copy', label: '复制', icon: '📋', disabled: !hasText },
        { key: 'select-all', label: '全选', icon: '🔲' },
        { key: 'translate', label: '翻译', icon: '🌐', disabled: !hasText },
        { key: 'explain', label: '解释', icon: '💡', disabled: !hasText },
      ],
    };
  }

  async function onMenuSelect(key) {
    const { text, item } = ctxMenu.value;
    switch (key) {
      case 'copy':
        if (text) {
          try { await navigator.clipboard.writeText(text); }
          catch { notificationStore.notifyError('复制失败'); }
        }
        break;
      case 'select-all':
        selectAll();
        break;
      case 'translate':
        await runTextAction('translate', text, item);
        break;
      case 'explain':
        await runTextAction('explain', text, item);
        break;
    }
  }

  function selectAll() {
    const el = listRef.value;
    if (!el) return;
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  /**
   * 翻译/解释选中文本，结果作为注解挂到对应条目（缩略按钮 + 弹层）。
   * 未命中具体条目时挂到最后一条。
   */
  async function runTextAction(type, text, item) {
    if (!text) return;
    const target = item || transcriptions.value[transcriptions.value.length - 1];
    if (!target) return;

    const annotation = reactive({
      id: Date.now() + Math.random(),
      type,
      source: text,
      text: '',
      loading: true,
      open: true,
    });
    if (!Array.isArray(target.annotations)) target.annotations = [];
    target.annotations.push(annotation);

    const fn = type === 'translate' ? translateText : explainText;
    const result = await fn(text);

    if (result.ok && result.content) {
      annotation.text = result.content;
      annotation.loading = false;
      workspaceStore.markTranscriptionEdited();
    } else {
      const idx = target.annotations.indexOf(annotation);
      if (idx !== -1) target.annotations.splice(idx, 1);
    }
  }

  function toggleAnnotation(annotation) {
    annotation.open = !annotation.open;
  }

  return {
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
  };
}
