<template>
  <div class="helpdoc-content">
    <aside class="helpdoc-toc" v-if="toc.length">
      <h4 class="helpdoc-toc-title">目录</h4>
      <a
        v-for="h in toc"
        :key="h.id"
        class="helpdoc-toc-item"
        :class="{ 'level-2': h.level === 2, 'level-3': h.level === 3 }"
        :href="'#' + h.id"
        @click.prevent="scrollTo(h.id)"
      >{{ h.text }}</a>
    </aside>
    <div class="helpdoc-body" ref="bodyRef" v-html="renderedHtml"></div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';

const bodyRef = ref(null);
const renderedHtml = ref('');
const toc = ref([]);

/**
 * 极简 Markdown → HTML 转换（无外部依赖）。
 * 支持标题、段落、列表、粗体、行内代码、代码块、分隔线。
 */
function renderMarkdown(md) {
  const headings = [];
  let headingIdx = 0;

  // 代码块占位
  const codeBlocks = [];
  let text = md.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
    const idx = codeBlocks.length;
    const escaped = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    codeBlocks.push(`<pre><code>${escaped}</code></pre>`);
    return `\n%%CODEBLOCK_${idx}%%\n`;
  });

  const lines = text.split('\n');
  const html = [];
  let inList = false;

  for (const line of lines) {
    // 代码块占位还原
    const cbMatch = line.match(/^%%CODEBLOCK_(\d+)%%$/);
    if (cbMatch) {
      if (inList) { html.push('</ul>'); inList = false; }
      html.push(codeBlocks[+cbMatch[1]]);
      continue;
    }

    // 标题
    const hMatch = line.match(/^(#{1,4})\s+(.+)$/);
    if (hMatch) {
      if (inList) { html.push('</ul>'); inList = false; }
      const level = hMatch[1].length;
      const text = inlineFormat(hMatch[2]);
      const id = `h-${headingIdx++}`;
      headings.push({ id, level, text: hMatch[2] });
      html.push(`<h${level} id="${id}">${text}</h${level}>`);
      continue;
    }

    // 分隔线
    if (/^[-*_]{3,}\s*$/.test(line)) {
      if (inList) { html.push('</ul>'); inList = false; }
      html.push('<hr>');
      continue;
    }

    // 无序列表
    const liMatch = line.match(/^[-*+]\s+(.+)$/);
    if (liMatch) {
      if (!inList) { html.push('<ul>'); inList = true; }
      html.push(`<li>${inlineFormat(liMatch[1])}</li>`);
      continue;
    }

    // 空行
    if (!line.trim()) {
      if (inList) { html.push('</ul>'); inList = false; }
      continue;
    }

    // 段落
    if (inList) { html.push('</ul>'); inList = false; }
    html.push(`<p>${inlineFormat(line)}</p>`);
  }

  if (inList) html.push('</ul>');

  toc.value = headings;
  return html.join('\n');
}

function inlineFormat(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>');
}

function scrollTo(id) {
  bodyRef.value?.querySelector(`#${id}`)?.scrollIntoView({ behavior: 'smooth' });
}

onMounted(async () => {
  try {
    const result = await window.electronAPI.getHelpDoc();
    if (result?.ok && result.content) {
      renderedHtml.value = renderMarkdown(result.content);
    } else {
      renderedHtml.value = '<p style="color:#999">暂无帮助文档</p>';
    }
  } catch {
    renderedHtml.value = '<p style="color:#999">加载帮助文档失败</p>';
  }
});
</script>

<style scoped src="./HelpDocContent.scoped.css"></style>
