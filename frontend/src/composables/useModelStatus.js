/**
 * 语音识别模型状态 — 检测模型是否已下载，并支持在 UI 引导下载。
 *
 * 单一职责：与后端交互查询/下载 Whisper 模型，暴露响应式状态供 UI 提示。
 * 打包发行版首次使用若模型缺失，可在界面提示用户下载。
 */

import { reactive } from 'vue';

export function useModelStatus() {
  const state = reactive({
    checked: false,     // 是否已完成首次检测
    present: true,      // 模型是否已就绪（默认 true，避免检测前误报）
    modelSize: '',
    downloading: false,
    message: '',
  });

  function check() {
    window.electronAPI?.sendControl?.('check_model');
  }

  function download() {
    if (state.downloading) return;
    state.downloading = true;
    state.message = '正在准备下载…';
    window.electronAPI?.sendControl?.('download_model');
  }

  function bind() {
    if (!window.electronAPI?.on) return () => {};
    const offStatus = window.electronAPI.on('python:model-status', (d) => {
      state.checked = true;
      state.present = !!d?.present;
      if (d?.model_size) state.modelSize = d.model_size;
    });
    const offProgress = window.electronAPI.on('python:model-download-progress', (d) => {
      state.downloading = true;
      if (d?.model_size) state.modelSize = d.model_size;
      state.message = d?.message || '正在下载语音模型…';
    });
    const offDone = window.electronAPI.on('python:model-download-done', (d) => {
      state.downloading = false;
      if (d?.ok) {
        state.present = true;
        state.message = '';
      } else {
        state.message = d?.error || '模型下载失败';
      }
    });
    return () => { offStatus?.(); offProgress?.(); offDone?.(); };
  }

  return { state, check, download, bind };
}
