<template>
  <div class="shortcuts-content">
    <div v-for="group in shortcuts" :key="group.label" class="sc-group">
      <h3 class="sc-group-label">{{ group.label }}</h3>
      <table class="sc-table">
        <tr v-for="item in group.items" :key="item.keys">
          <td class="sc-keys">
            <kbd v-for="(k, i) in item.keys.split('+')" :key="i">{{ k.trim() }}</kbd>
          </td>
          <td class="sc-desc">{{ item.desc }}</td>
        </tr>
      </table>
    </div>
  </div>
</template>

<script setup>
const shortcuts = [
  {
    label: '会议',
    items: [
      { keys: 'Ctrl + Shift + S', desc: '开始会议' },
      { keys: 'Ctrl + Shift + X', desc: '停止会议' },
      { keys: 'Ctrl + S', desc: '保存工作区' },
      { keys: 'Ctrl + H', desc: '历史会议记录' },
    ],
  },
  {
    label: '音频',
    items: [
      { keys: 'Ctrl + Shift + D', desc: '音频设备监控面板' },
    ],
  },
  {
    label: '视图',
    items: [
      { keys: 'Ctrl + Shift + O', desc: '悬浮字幕' },
      { keys: 'Ctrl + Shift + M', desc: '闭麦表达助手' },
      { keys: 'Ctrl + Shift + P', desc: '会议摘要面板' },
    ],
  },
  {
    label: '帮助',
    items: [
      { keys: 'F1', desc: '使用帮助' },
      { keys: 'Ctrl + /', desc: '快捷键一览' },
    ],
  },
];
</script>

<style scoped src="./ShortcutsContent.scoped.css"></style>
