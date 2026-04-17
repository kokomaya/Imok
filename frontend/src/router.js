/**
 * 简易 hash 路由器。
 *
 * 单一职责：根据 URL hash 返回当前路由名称。
 * 支持主窗口（默认）和悬浮窗（#/overlay）两个视图。
 *
 * 不依赖 vue-router，避免过度引入依赖。
 */

import { ref, onMounted, onUnmounted } from 'vue';

/**
 * 解析当前 hash 路由。
 * @returns {string} 路由名称（'main' | 'overlay'）
 */
function parseRoute() {
  const hash = window.location.hash.replace('#', '');
  if (hash === '/overlay') return 'overlay';
  return 'main';
}

/**
 * 使用 hash 路由的组合式函数。
 * @returns {{ currentRoute: import('vue').Ref<string> }}
 */
export function useHashRoute() {
  const currentRoute = ref(parseRoute());

  function onHashChange() {
    currentRoute.value = parseRoute();
  }

  onMounted(() => {
    window.addEventListener('hashchange', onHashChange);
  });

  onUnmounted(() => {
    window.removeEventListener('hashchange', onHashChange);
  });

  return { currentRoute };
}
