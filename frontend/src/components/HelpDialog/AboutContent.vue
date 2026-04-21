<template>
  <div class="about-content">
    <div class="about-logo">📝</div>
    <h2 class="about-name">{{ info.displayName }}</h2>
    <p class="about-desc">{{ info.description }}</p>
    <p class="about-version">版本 {{ info.version }}</p>

    <div class="about-section">
      <h3>作者</h3>
      <p>
        <span>{{ info.author.name }}</span>
        <a class="about-link" href="#" @click.prevent="openUrl(info.author.url)">GitHub</a>
      </p>
    </div>

    <div class="about-section">
      <h3>项目仓库</h3>
      <p>
        <a class="about-link" href="#" @click.prevent="openUrl(info.repository)">{{ info.repository }}</a>
      </p>
    </div>

    <div class="about-section">
      <h3>运行环境</h3>
      <table class="about-env">
        <tbody>
          <tr><td>Electron</td><td>{{ info.electron }}</td></tr>
          <tr><td>Chromium</td><td>{{ info.chrome }}</td></tr>
          <tr><td>Node.js</td><td>{{ info.node }}</td></tr>
          <tr><td>V8</td><td>{{ info.v8 }}</td></tr>
        </tbody>
      </table>
    </div>

    <p class="about-license">License: {{ info.license }}</p>
  </div>
</template>

<script setup>
import { reactive, onMounted } from 'vue';

const info = reactive({
  displayName: 'Imok',
  version: '-',
  description: '',
  author: { name: '', url: '' },
  repository: '',
  license: '',
  electron: '',
  chrome: '',
  node: '',
  v8: '',
});

onMounted(async () => {
  try {
    const data = await window.electronAPI.getAppInfo();
    Object.assign(info, data);
  } catch { /* fallback to defaults */ }
});

function openUrl(url) {
  window.electronAPI.openExternal(url);
}
</script>

<style scoped src="./AboutContent.scoped.css"></style>
