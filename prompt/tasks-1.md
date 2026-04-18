# Tasks-1: 实现方案

> 对应 todo.md 中的 5 项需求，遵循 SOLID 原则设计。

---

## Task 1: 去除框架相关内容（自定义无边框窗口）

### 目标
移除 Electron 默认窗口边框（标题栏、菜单栏），换成自定义极简拖拽区域，使 UI 更沉浸。

### 设计原则
- **SRP**: 拖拽区域作为独立组件 `TitleBar.vue`，只负责窗口控制（拖拽/最小化/最大化/关闭）
- **OCP**: 窗口控制按钮可通过 slot 或 props 扩展，不影响 App.vue 主逻辑
- **DIP**: TitleBar 通过 `electronAPI` 抽象调用窗口操作，不直接依赖 Node API

### 修改清单

#### 1.1 `frontend/electron/main.js`
```js
// BrowserWindow 配置修改
mainWindow = new BrowserWindow({
  width: 480, height: 720,
  minWidth: 360, minHeight: 480,
  frame: false,              // 移除系统窗口框架
  titleBarStyle: 'hidden',   // macOS: 隐藏标题栏但保留红绿灯
  // ... 其余不变
});
```
- 新增 IPC handler: `window:minimize`, `window:maximize`, `window:close`
```js
ipcMain.on('window:minimize', () => mainWindow?.minimize());
ipcMain.on('window:maximize', () => {
  if (mainWindow?.isMaximized()) mainWindow.unmaximize();
  else mainWindow?.maximize();
});
ipcMain.on('window:close', () => mainWindow?.close());
```
- 移除默认菜单: `Menu.setApplicationMenu(null)`

#### 1.2 `frontend/electron/preload.js`
```js
// 新增窗口控制方法到白名单
minimizeWindow: () => ipcRenderer.send('window:minimize'),
maximizeWindow: () => ipcRenderer.send('window:maximize'),
closeWindow:    () => ipcRenderer.send('window:close'),
```

#### 1.3 新建 `frontend/src/components/TitleBar.vue`
```
┌─────────────────────────────────────────────┐
│ [drag region ···························]  ─ □ × │
└─────────────────────────────────────────────┘
```
- 整行 `-webkit-app-region: drag`
- 右侧三个按钮（最小化/最大化/关闭）设为 `-webkit-app-region: no-drag`
- 高度 32px，背景 `#fafafa`，底边框 `1px solid #e0e0e0`
- 按钮用纯 CSS 图标（`─` `□` `×`），不引入图标库

#### 1.4 `frontend/src/App.vue`
- 在模板最顶部插入 `<TitleBar />`（在 `.app` div 内第一个元素）
- `.header` 移除重复的拖拽样式

### 测试要点
- 拖拽区域可正常拖动窗口
- 三个窗口控制按钮功能正常
- 双击拖拽区域最大化/还原
- overlay 窗口不受影响（已经是 `frame: false`）

---

## Task 2: 工具栏图标化 & 菜单化

### 目标
将 header 中 5 个文字按钮替换为图标按钮，部分功能收入下拉菜单，减少视觉噪音。

### 设计原则
- **SRP**: 下拉菜单作为独立组件 `DropdownMenu.vue`，只负责展示/收起和点击派发
- **OCP**: 菜单项通过数组 prop 配置，新增菜单项无需改组件内部
- **ISP**: 图标按钮组件 `IconButton.vue` 只暴露 `icon`/`label`/`active`/`@click`，不耦合业务逻辑

### UI 设计

当前布局:
```
[🔊 系统音频] [字幕窗] [闭麦助手] [摘要] [📂 历史] ● running
```

新布局（图标 + tooltip）:
```
[🔊] [🎯] [💬] [📝] [⋮]  ● running
 ↑    ↑    ↑    ↑    ↑
音频  字幕  闭麦  摘要  更多菜单
                       ├─ 📂 历史记录
                       ├─ ⚙ 设置(预留)
                       └─ ℹ 关于(预留)
```

### 修改清单

#### 2.1 新建 `frontend/src/components/ui/IconButton.vue`
```vue
<template>
  <button class="icon-btn" :class="{ active }" :title="label" @click="$emit('click')">
    <slot />
  </button>
</template>
```
- Props: `label: string`, `active: boolean`
- 样式: 32×32px, 圆角 6px, hover 背景 `#e8e8e8`, active 背景 `#d0d0ff`
- tooltip 通过原生 `title` 属性

#### 2.2 新建 `frontend/src/components/ui/DropdownMenu.vue`
```vue
<template>
  <div class="dropdown" v-click-outside="close">
    <IconButton :label="label" @click="toggle"><slot name="trigger" /></IconButton>
    <div v-if="open" class="dropdown-panel">
      <button v-for="item in items" :key="item.id" @click="select(item)">
        <span class="menu-icon">{{ item.icon }}</span>
        <span>{{ item.label }}</span>
      </button>
    </div>
  </div>
</template>
```
- Props: `items: Array<{ id, icon, label }>`, `label: string`
- Events: `@select(item)`
- 面板: 绝对定位，右对齐，阴影 `0 4px 12px rgba(0,0,0,0.15)`，白底，圆角 8px
- `v-click-outside`: 用自定义指令实现（~10行），点击外部自动关闭

#### 2.3 `frontend/src/App.vue`
- 替换 `.header-actions` 内的 5 个 `<button>` 为 4 个 `<IconButton>` + 1 个 `<DropdownMenu>`
- 图标使用 emoji（无需引入图标库，保持零依赖）:
  - 音频源: `🔊`/`🎤`（根据当前 `audioSource` 切换）
  - 字幕窗: `🎯`
  - 闭麦助手: `💬`
  - 摘要: `📝`
  - 更多: `⋮`（竖三点 `\u22EE`）
- DropdownMenu items:
  ```js
  const menuItems = [
    { id: 'history', icon: '📂', label: '历史记录' },
    // 未来扩展：
    // { id: 'settings', icon: '⚙', label: '设置' },
  ]
  ```

### 测试要点
- 所有按钮 hover 显示中文 tooltip
- 下拉菜单点击外部自动关闭
- 功能行为与改造前完全一致
- 窗口宽度 360px（最小宽度）时不溢出

---

## Task 3: Ctrl + 滑轮缩放

### 目标
用户可通过 `Ctrl+滚轮` 缩放整个应用 UI，并持久化缩放级别。

### 设计原则
- **SRP**: 缩放逻辑封装在独立模块 `zoom-manager.js` 中，不侵入 App.vue
- **DIP**: 通过 `electronAPI` 抽象层通信，渲染进程不直接调用 `webFrame`

### 修改清单

#### 3.1 `frontend/electron/main.js`
```js
// 新增 IPC handler
ipcMain.handle('zoom:get', () => mainWindow?.webContents.getZoomFactor());
ipcMain.handle('zoom:set', (_, factor) => {
  const clamped = Math.max(0.5, Math.min(2.0, factor));
  mainWindow?.webContents.setZoomFactor(clamped);
  return clamped;
});
```
- 读取/保存缩放级别到 `electron-store` 或简单的 `localStorage`（通过 IPC）
- 窗口创建后恢复上次缩放级别

#### 3.2 `frontend/electron/preload.js`
```js
getZoom: () => ipcRenderer.invoke('zoom:get'),
setZoom: (factor) => ipcRenderer.invoke('zoom:set', factor),
```

#### 3.3 新建 `frontend/src/services/zoom-manager.js`
```js
const STEP = 0.1;
const MIN = 0.5;
const MAX = 2.0;

export function initZoom() {
  // 恢复上次缩放级别
  const saved = localStorage.getItem('zoom-factor');
  if (saved) window.electronAPI?.setZoom(parseFloat(saved));

  // 监听 Ctrl+滚轮
  window.addEventListener('wheel', (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    const delta = e.deltaY > 0 ? -STEP : STEP;
    window.electronAPI?.getZoom().then(current => {
      const next = Math.round((current + delta) * 10) / 10;
      window.electronAPI?.setZoom(next).then(actual => {
        localStorage.setItem('zoom-factor', String(actual));
      });
    });
  }, { passive: false });

  // Ctrl+0 重置
  window.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === '0') {
      e.preventDefault();
      window.electronAPI?.setZoom(1.0);
      localStorage.setItem('zoom-factor', '1.0');
    }
  });
}
```

#### 3.4 `frontend/src/App.vue`
```js
import { initZoom } from './services/zoom-manager.js';
onMounted(() => {
  initZoom();
  // ... existing code
});
```

### 快捷键
| 操作 | 快捷键 |
|------|--------|
| 放大 | `Ctrl + 滚轮上` 或 `Ctrl + =` |
| 缩小 | `Ctrl + 滚轮下` 或 `Ctrl + -` |
| 重置 | `Ctrl + 0` |

### 测试要点
- 缩放范围 50%–200%，步进 10%
- 缩放后布局不崩坏（flex 布局天然支持）
- 刷新/重启后缩放级别恢复
- overlay 窗口不受主窗口缩放影响

---

## Task 4: 识别不同的音色，分辨角色 ✅ 已完成

### 当前状态
已实现完整的说话人分识（speaker diarization）流水线：

| 层 | 文件 | 职责 |
|----|------|------|
| 抽象接口 | `backend/speaker/base.py` | `SpeakerEmbedderBase` / `SpeakerTrackerBase` ABC |
| 嵌入提取 | `backend/speaker/embedder.py` | SpeechBrain ECAPA-TDNN → 192 维向量 |
| 在线跟踪 | `backend/speaker/tracker.py` | 增量余弦相似度聚类，阈值 0.65 |
| 管道集成 | `backend/pipeline/meeting_pipeline.py` | `_process_segment` 中可选执行嵌入 + 识别 |
| IPC 传输 | `backend/ipc/messages.py` | `TranscriptionData.speaker` 字段 |
| 持久化 | `backend/storage/meeting_store.py` | `save_speakers()` / `load_speakers()` → `speakers.json` |
| 前端展示 | `frontend/src/App.vue` | `[Speaker_X]` 紫色标签 |
| 摘要提示 | `backend/llm/prompt_manager.py` | 指示 LLM 标注发言人观点 |

### SOLID 合规
- **SRP**: Embedder 只提取向量，Tracker 只分配 ID
- **OCP/DIP**: Pipeline 依赖 ABC（`SpeakerEmbedderBase`），可替换为 PyAnnote/WeSpeaker 等实现
- **LSP**: 具体实现可无缝替换

### 无需额外工作

---

## Task 5: 英文表达优化建议

### 目标
在转写面板中，当检测到用户可能在尝试用英文发言时，提供实时的英文表达优化建议（类似 Grammarly 内联建议），或者对中文发言提供英文表达参考。

### 设计原则
- **SRP**: 表达优化服务 `ExpressionAdvisor` 独立于翻译和摘要
- **OCP**: 通过策略模式支持不同优化类型（语法修正/中译英/表达润色），新增类型不改已有代码
- **DIP**: 前端通过已有 `llm:chat` IPC 代理调用 LLM，不直接耦合后端实现

### 功能拆分

#### 模式 A: 实时转写优化（自动）
- 当 ASR 识别到英文发言（`language === 'en'`）时，自动发送到 LLM 做润色
- 在转写条目下方显示优化建议（浅蓝色背景，小字体）
- 用户可点击"采纳"复制到剪贴板

#### 模式 B: 手动请求（已有闭麦助手的增强）
- 现有 `MuteAssistPanel` 已支持中文→英文转换
- 增强: 支持英文→更地道英文的润色模式

### 修改清单

#### 5.1 新建 `frontend/src/services/expression-advisor.js`
```js
/**
 * 英文表达优化顾问 — 对转写文本提供表达建议。
 *
 * 策略模式：根据输入语言选择不同的优化策略。
 * - 英文输入 → 润色/语法修正
 * - 中文输入 → 提供英文表达参考
 */
export class ExpressionAdvisor {
  constructor(llmChat) { this._llmChat = llmChat; }

  async suggest(text, language) {
    if (!text.trim()) return null;
    const strategy = language === 'en' ? 'polish' : 'translate';
    const prompt = STRATEGY_PROMPTS[strategy].replace('{text}', text);
    const result = await this._llmChat(SYSTEM_PROMPT, prompt);
    return { original: text, suggestion: result, strategy };
  }
}

const SYSTEM_PROMPT = `你是会议场景的英文表达顾问。
- 如果输入是英文，修正语法并润色表达，使其更专业自然
- 如果输入是中文，提供地道的英文会议表达
- 只输出优化后的英文，不要解释`;

const STRATEGY_PROMPTS = {
  polish: '请润色以下英文会议发言，修正语法并使表达更专业：\n\n{text}',
  translate: '请将以下中文转为地道的英文会议表达：\n\n{text}',
};
```

#### 5.2 `frontend/src/App.vue`
- 新增 ref: `expressionSuggestions = ref(new Map())` — key 为 transcription id
- 新增切换: `autoSuggest = ref(false)` — 是否自动优化
- 在工具栏的 DropdownMenu 中新增菜单项: `{ id: 'auto-suggest', icon: '💡', label: '英文表达建议' }`（切换 `autoSuggest`）
- 在 `python:transcription` 回调中:
  ```js
  if (autoSuggest.value) {
    advisor.suggest(data.text, data.language).then(result => {
      if (result) expressionSuggestions.value.set(itemId, result);
    });
  }
  ```
- 模板中转写条目增加建议展示:
  ```vue
  <div v-if="expressionSuggestions.get(item.id)" class="suggestion">
    <span class="suggestion-icon">💡</span>
    <span class="suggestion-text">{{ expressionSuggestions.get(item.id).suggestion }}</span>
    <button class="btn-copy" @click="copyText(expressionSuggestions.get(item.id).suggestion)">📋</button>
  </div>
  ```

#### 5.3 样式
```css
.suggestion {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 2px 0 4px 20px;
  padding: 4px 8px;
  background: #e3f2fd;
  border-radius: 4px;
  font-size: 12px;
  color: #1565c0;
}
```

#### 5.4 （可选）后端 prompt 模板扩展 `backend/llm/prompt_manager.py`
- 新增 `_POLISH_SYSTEM_PROMPT` / `_POLISH_USER_PROMPT` — 英文润色场景专用提示词
- 当前 `_EXPRESSION_SYSTEM_PROMPT` 已涵盖中→英，可复用
- 新增 prompt type `"polish"` 到 `PromptManager.get_prompt()` 方法

### 节流策略
- 不是每条转写都触发 LLM（太频繁），采用防抖：同一说话人连续多条合并后再请求
- 最多缓存最近 20 条建议，旧的自动清理
- LLM 请求并发限制 1（串行队列），避免阻塞摘要通道

### 测试要点
- 开关切换后立即生效/停止
- LLM 离线时静默降级（不显示错误）
- 建议内容可复制到剪贴板
- 不影响转写流和摘要流的性能

---

## 实施优先级

| 顺序 | Task | 复杂度 | 依赖 |
|------|------|--------|------|
| 1 | Task 1: 去除框架 | 低 | 无 |
| 2 | Task 2: 工具栏图标化/菜单化 | 中 | Task 1（TitleBar 影响布局） |
| 3 | Task 3: Ctrl+滚轮缩放 | 低 | 无 |
| 4 | Task 4: 音色识别 | — | ✅ 已完成 |
| 5 | Task 5: 英文表达建议 | 中 | Task 2（菜单项入口） |
