<script setup>
/**
 * 会议摘要预览对话框。
 *
 * 单一职责：以简洁优雅的模态形式展示完整会议摘要（Markdown 渲染），
 * 支持一键复制与多种优雅关闭方式（关闭按钮 / ESC / 点击遮罩）。
 */

import { ref, computed, watch, onMounted, onUnmounted } from 'vue';

const props = defineProps({
  visible: { type: Boolean, default: false },
  markdown: { type: String, default: '' },
});
const emit = defineEmits(['close']);

const copied = ref(false);
let copyTimer = null;

const renderedHtml = computed(() => renderMarkdown(props.markdown || ''));

const isEmpty = computed(() => !props.markdown || !props.markdown.trim());

function close() {
  emit('close');
}

async function onCopy() {
  const { copyToClipboard } = await import('./summaryExport.js');
  const ok = await copyToClipboard(props.markdown || '');
  if (ok) {
    copied.value = true;
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => { copied.value = false; }, 1800);
  }
}

function onKeydown(e) {
  if (props.visible && e.key === 'Escape') {
    e.stopPropagation();
    close();
  }
}

watch(() => props.visible, (v) => {
  if (!v) copied.value = false;
});

onMounted(() => window.addEventListener('keydown', onKeydown, true));
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown, true);
  clearTimeout(copyTimer);
});

// ── 极简 Markdown → HTML（无外部依赖，先转义再格式化，避免 XSS） ──

function inlineFormat(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>');
}

function renderMarkdown(md) {
  const lines = md.split('\n');
  const html = [];
  let inList = false;

  const closeList = () => {
    if (inList) { html.push('</ul>'); inList = false; }
  };

  for (const line of lines) {
    const hMatch = line.match(/^(#{1,4})\s+(.+)$/);
    if (hMatch) {
      closeList();
      const level = Math.min(hMatch[1].length, 4);
      html.push(`<h${level}>${inlineFormat(hMatch[2])}</h${level}>`);
      continue;
    }
    if (/^[-*_]{3,}\s*$/.test(line)) {
      closeList();
      html.push('<hr>');
      continue;
    }
    const liMatch = line.match(/^[-*+]\s+(.+)$/);
    if (liMatch) {
      if (!inList) { html.push('<ul>'); inList = true; }
      html.push(`<li>${inlineFormat(liMatch[1])}</li>`);
      continue;
    }
    if (!line.trim()) {
      closeList();
      continue;
    }
    closeList();
    html.push(`<p>${inlineFormat(line)}</p>`);
  }
  closeList();
  return html.join('\n');
}
</script>

<template>
  <transition name="spv-fade">
    <div v-if="visible" class="spv-backdrop" @click.self="close">
      <div class="spv-dialog" role="dialog" aria-modal="true">
        <header class="spv-header">
          <span class="spv-title">📄 会议摘要预览</span>
          <div class="spv-actions">
            <button class="spv-copy" :class="{ done: copied }" @click="onCopy" title="复制全部内容">
              {{ copied ? '✓ 已复制' : '📋 复制' }}
            </button>
            <button class="spv-close" @click="close" title="关闭 (Esc)">✕</button>
          </div>
        </header>
        <div class="spv-body">
          <div v-if="isEmpty" class="spv-empty">
            <div class="spv-empty-icon">📭</div>
            <div>当前还没有可预览的摘要内容</div>
          </div>
          <div v-else class="spv-markdown" v-html="renderedHtml"></div>
        </div>
      </div>
    </div>
  </transition>
</template>

<style scoped src="./SummaryPreviewDialog.scoped.css"></style>
