/**
 * Electron 前端集成验证脚本 — Node.js 环境运行。
 *
 * 单一职责：验证 PythonBridge IPC 通信和前端模块完整性。
 * 不依赖 Electron runtime（直接用 Node.js 执行可测试的部分）。
 *
 * 使用方式：
 *   node frontend/scripts/verify-integration.mjs
 */

import { readFileSync, existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = resolve(__dirname, '..');
const PROJECT_DIR = resolve(FRONTEND_DIR, '..');

let passed = 0;
let failed = 0;

function check(name, fn) {
  try {
    fn();
    console.log(`  ✅ ${name}`);
    passed++;
  } catch (e) {
    console.log(`  ❌ ${name}: ${e.message}`);
    failed++;
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

// ═══════════════════════════════════════════════════════════════
// 1. 前端模块文件完整性
// ═══════════════════════════════════════════════════════════════

console.log('\n══════════════════════════════════════════════════════');
console.log('  [1/4] Frontend Module Integrity');
console.log('══════════════════════════════════════════════════════');

const requiredFiles = [
  'electron/main.js',
  'electron/preload.js',
  'electron/python-bridge.js',
  'electron/window-manager.js',
  'src/App.vue',
  'src/main.js',
  'src/router.js',
  'src/services/ipc-bridge.js',
  'src/services/llm-client.js',
  'src/services/expression-service.js',
  'src/stores/subtitle-store.js',
  'src/stores/mute-assist-store.js',
  'src/components/SubtitleOverlay/SubtitleOverlay.vue',
  'src/components/SubtitleOverlay/index.js',
  'src/components/MuteAssistPanel/MuteAssistPanel.vue',
  'src/components/MuteAssistPanel/index.js',
  'src/assets/main.css',
  'package.json',
  'vite.config.js',
  'index.html',
  'electron-builder.config.js',
];

for (const file of requiredFiles) {
  check(`File exists: ${file}`, () => {
    const fullPath = resolve(FRONTEND_DIR, file);
    assert(existsSync(fullPath), `Missing: ${fullPath}`);
  });
}

// ═══════════════════════════════════════════════════════════════
// 2. IPC 通道白名单验证
// ═══════════════════════════════════════════════════════════════

console.log('\n══════════════════════════════════════════════════════');
console.log('  [2/4] IPC Channel Whitelist Verification');
console.log('══════════════════════════════════════════════════════');

const preloadContent = readFileSync(resolve(FRONTEND_DIR, 'electron/preload.js'), 'utf-8');

const expectedInvokeChannels = [
  'python:control',
  'python:start',
  'python:stop',
  'python:status',
  'overlay:open',
  'overlay:close',
  'overlay:set-click-through',
  'overlay:set-always-on-top',
  'mute-panel:toggle',
];

const expectedReceiveChannels = [
  'python:transcription',
  'python:status',
  'python:error',
  'python:exit',
  'python:restart',
  'python:bridge-error',
  'mute-panel:toggle',
];

for (const ch of expectedInvokeChannels) {
  check(`Invoke channel: ${ch}`, () => {
    assert(preloadContent.includes(`'${ch}'`), `Channel '${ch}' not found in preload INVOKE_CHANNELS`);
  });
}

for (const ch of expectedReceiveChannels) {
  check(`Receive channel: ${ch}`, () => {
    assert(preloadContent.includes(`'${ch}'`), `Channel '${ch}' not found in preload RECEIVE_CHANNELS`);
  });
}

check('Preload API: toggleMutePanel', () => {
  assert(preloadContent.includes('toggleMutePanel'), 'Missing toggleMutePanel API method');
});

check('Preload: contextIsolation + sandbox', () => {
  // contextIsolation and sandbox are set in main.js, but preload uses contextBridge
  assert(preloadContent.includes('contextBridge'), 'Missing contextBridge usage');
});

// ═══════════════════════════════════════════════════════════════
// 3. Electron 主进程配置验证
// ═══════════════════════════════════════════════════════════════

console.log('\n══════════════════════════════════════════════════════');
console.log('  [3/4] Electron Main Process Configuration');
console.log('══════════════════════════════════════════════════════');

const mainContent = readFileSync(resolve(FRONTEND_DIR, 'electron/main.js'), 'utf-8');

check('Security: contextIsolation enabled', () => {
  assert(mainContent.includes('contextIsolation: true'), 'contextIsolation should be true');
});

check('Security: nodeIntegration disabled', () => {
  assert(mainContent.includes('nodeIntegration: false'), 'nodeIntegration should be false');
});

check('Security: sandbox enabled', () => {
  assert(mainContent.includes('sandbox: true'), 'sandbox should be true');
});

check('PythonBridge integration', () => {
  assert(mainContent.includes("require('./python-bridge')"), 'Missing PythonBridge import');
  assert(mainContent.includes('initPythonBridge'), 'Missing initPythonBridge call');
});

check('WindowManager integration', () => {
  assert(mainContent.includes("require('./window-manager')"), 'Missing WindowManager import');
});

check('Global shortcut: Ctrl+Shift+M', () => {
  assert(mainContent.includes('CommandOrControl+Shift+M'), 'Missing Ctrl+Shift+M shortcut');
  assert(mainContent.includes('globalShortcut'), 'Missing globalShortcut import');
  assert(mainContent.includes('registerShortcuts'), 'Missing registerShortcuts function');
});

check('Shortcut cleanup on quit', () => {
  assert(mainContent.includes('unregisterAll'), 'Missing globalShortcut.unregisterAll on quit');
});

check('IPC broadcast to overlay', () => {
  assert(mainContent.includes('broadcast'), 'Missing broadcast helper');
  assert(mainContent.includes('python:transcription'), 'Missing transcription broadcast');
});

// ═══════════════════════════════════════════════════════════════
// 4. PythonBridge JSON Lines 协议验证
// ═══════════════════════════════════════════════════════════════

console.log('\n══════════════════════════════════════════════════════');
console.log('  [4/4] PythonBridge Protocol Verification');
console.log('══════════════════════════════════════════════════════');

const bridgeContent = readFileSync(resolve(FRONTEND_DIR, 'electron/python-bridge.js'), 'utf-8');

check('PythonBridge: spawns subprocess mode', () => {
  assert(bridgeContent.includes('--mode=subprocess'), 'Missing --mode=subprocess spawn arg');
});

check('PythonBridge: JSON Lines parsing', () => {
  assert(bridgeContent.includes('JSON.parse'), 'Missing JSON.parse for stdout parsing');
});

check('PythonBridge: message dispatch', () => {
  assert(bridgeContent.includes('transcription'), 'Missing transcription event dispatch');
  assert(bridgeContent.includes('status'), 'Missing status event dispatch');
});

check('PythonBridge: crash restart', () => {
  assert(bridgeContent.includes('restart') || bridgeContent.includes('Restart'),
    'Missing crash restart logic');
});

check('PythonBridge: max restart limit', () => {
  assert(bridgeContent.includes('maxRestarts') || bridgeContent.includes('_maxRestarts'),
    'Missing max restart limit');
});

// ═══════════════════════════════════════════════════════════════
// Component integration checks
// ═══════════════════════════════════════════════════════════════

const appVue = readFileSync(resolve(FRONTEND_DIR, 'src/App.vue'), 'utf-8');

check('App.vue: MuteAssistPanel import', () => {
  assert(appVue.includes('MuteAssistPanel'), 'Missing MuteAssistPanel in App.vue');
});

check('App.vue: mute-panel toggle listener', () => {
  assert(appVue.includes('mute-panel:toggle'), 'Missing mute-panel:toggle listener');
});

check('App.vue: SubtitleOverlay for overlay route', () => {
  assert(appVue.includes('SubtitleOverlay'), 'Missing SubtitleOverlay component');
});

// CSP check in index.html
const indexHtml = readFileSync(resolve(FRONTEND_DIR, 'index.html'), 'utf-8');

check('index.html: CSP with connect-src', () => {
  assert(indexHtml.includes('connect-src'), 'Missing connect-src in CSP for LLM API access');
});

// ═══════════════════════════════════════════════════════════════
// Summary
// ═══════════════════════════════════════════════════════════════

console.log('\n══════════════════════════════════════════════════════');
const total = passed + failed;
const color = failed === 0 ? '\x1b[92m' : '\x1b[91m';
const reset = '\x1b[0m';
console.log(`  Result: ${color}${passed}/${total} checks passed${reset}`);
if (failed > 0) {
  console.log(`  ${failed} checks failed`);
  process.exit(1);
}
console.log('══════════════════════════════════════════════════════\n');
process.exit(0);
