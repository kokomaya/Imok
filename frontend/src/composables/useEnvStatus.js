/**
 * 轻量版运行环境状态 — 检测 Python 依赖是否就绪，并支持在 UI 引导一键安装。
 *
 * 单一职责：与主进程交互检测 .venv/依赖，暴露响应式状态供 UI 提示。
 * 轻量发行版首次运行若依赖缺失，可在界面引导用户一键安装（创建 .venv + pip install）。
 */

import { reactive } from 'vue';

export function useEnvStatus() {
  const state = reactive({
    checked: false,     // 是否已完成首次检测
    ready: true,        // 依赖是否就绪（默认 true，避免检测前误报）
    mode: '',           // dev / full / lite
    reason: '',         // venv_missing / deps_missing
    missing: '',        // 缺失的模块名（若可解析）
    installing: false,
    logLines: [],       // 安装进度输出（最近若干行）
    error: '',
  });

  async function check() {
    const res = await window.electronAPI?.checkEnv?.();
    state.checked = true;
    if (!res) return;
    state.ready = !!res.ready;
    state.mode = res.mode || '';
    state.reason = res.reason || '';
    state.missing = res.missing || '';
  }

  async function install() {
    if (state.installing) return;
    state.installing = true;
    state.error = '';
    state.logLines = ['正在准备安装依赖环境…'];
    await window.electronAPI?.installEnv?.();
  }

  function bind() {
    if (!window.electronAPI?.on) return () => {};
    const offProgress = window.electronAPI.on('python:env-install-progress', (d) => {
      state.installing = true;
      if (d?.line) {
        state.logLines.push(d.line);
        if (state.logLines.length > 200) state.logLines.splice(0, state.logLines.length - 200);
      }
    });
    const offDone = window.electronAPI.on('python:env-install-done', async (d) => {
      state.installing = false;
      if (d?.ok) {
        state.error = '';
        await check();
      } else {
        state.error = d?.error || `安装失败（退出码 ${d?.code ?? '未知'}）`;
      }
    });
    return () => { offProgress?.(); offDone?.(); };
  }

  return { state, check, install, bind };
}
