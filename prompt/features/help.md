# 帮助菜单实现计划

## 一、功能清单

| # | 菜单项 | 行为 | 快捷键 |
|---|--------|------|--------|
| 1 | 使用帮助 | 打开本地 Markdown 帮助文档（渲染为 HTML 弹窗） | `F1` |
| 2 | 快捷键一览 | 弹窗展示所有快捷键列表 | `CmdOrCtrl+/` |
| 3 | 检查更新… | 请求 GitHub Releases API，对比本地版本号 | — |
| 4 | 反馈问题 | 用默认浏览器打开 GitHub Issues 页面 | — |
| 5 | 关于 Imok | 弹窗显示版本号、构建信息、技术栈摘要 | — |

---

## 二、架构设计（遵循 SOLID）

### 2.1 整体分层

```
Electron 主进程                   渲染进程 (Vue)
─────────────────                ──────────────────
app-menu.js                      App.vue
  └─ 帮助菜单项                     └─ handleMenuAction()
       │                                 │
       │  menu:action IPC                │ 分发
       ├──────────────────────────►──────┤
       │                                 ▼
       │                          HelpDialog.vue  ← 通用弹窗容器
       │                            ├─ AboutContent.vue
       │                            ├─ ShortcutsContent.vue
       │                            └─ HelpDocContent.vue
       │
       │ (纯主进程操作，不经过渲染进程)
       ├─ shell.openExternal()  ← 反馈问题
       └─ update-checker.js     ← 检查更新
```

### 2.2 职责划分

#### S — 单一职责

| 模块 | 职责 | 路径 |
|------|------|------|
| **app-menu.js** | 仅新增帮助菜单项，点击时发 IPC 或调 shell | `electron/app-menu.js`（扩展已有） |
| **update-checker.js** | 封装 GitHub Releases API 调用 + 版本比较 | `electron/update-checker.js`（新建） |
| **app-info.js** | 导出静态应用元数据（版本、作者、仓库 URL 等） | `electron/app-info.js`（新建） |
| **HelpDialog.vue** | 通用模态弹窗壳：遮罩、关闭按钮、标题栏、slot 内容 | `components/HelpDialog/HelpDialog.vue`（新建） |
| **AboutContent.vue** | 关于面板：logo、版本、作者、链接 | `components/HelpDialog/AboutContent.vue`（新建） |
| **ShortcutsContent.vue** | 快捷键表格渲染 | `components/HelpDialog/ShortcutsContent.vue`（新建） |
| **HelpDocContent.vue** | Markdown 帮助文档渲染 | `components/HelpDialog/HelpDocContent.vue`（新建） |
| **help-store.js** | 弹窗可见性 + 当前 tab 状态 | `stores/help-store.js`（新建） |

#### O — 开放封闭

- `HelpDialog` 使用 **具名插槽 / 动态组件** (`<component :is>`) 渲染内容 tab，新增内容页无需修改容器。
- 快捷键数据以 **声明式 JSON 数组** 维护，`ShortcutsContent` 纯展示，新增快捷键只需改数据。

#### L — 里氏替换

- 帮助弹窗内容页均为无状态展示组件，接受 `props` 输出 UI，可独立替换或测试。

#### I — 接口隔离

- `app-info.js` 仅导出 `getAppInfo()` 返回 `{ name, version, description, author, homepage, repository }`，不依赖 Electron API。
- `update-checker.js` 仅导出 `checkForUpdate(currentVersion): Promise<{ hasUpdate, latest, url } | null>`，不涉及 UI。

#### D — 依赖反转

- 渲染进程通过 `preload.js` 暴露的 `window.electronAPI.getAppInfo()` 获取元数据，不直接读 `package.json`。
- 主进程模块通过 `init()` 注入上下文（沿用 `app-menu.js` 现有模式），不硬编码全局引用。

---

## 三、详细实现步骤

### Phase 1：基础设施 — 应用元数据 & 通用弹窗

**1.1 `electron/app-info.js`**
```js
// 读取 package.json，导出标准元数据对象
function getAppInfo() {
  const pkg = require('../package.json');
  return {
    name: pkg.name,
    displayName: 'Imok',
    version: pkg.version,
    description: pkg.description,
    author: { name: 'kokomaya', url: 'https://github.com/kokomaya' },
    repository: 'https://github.com/kokomaya/Imok',
    license: 'MIT',
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
  };
}
```

**1.2 `preload.js` 新增通道**
- INVOKE_CHANNELS 白名单加入 `'app:info'`
- 暴露 `getAppInfo: () => ipcRenderer.invoke('app:info')`

**1.3 `main.js` 注册 IPC handler**
```js
ipcMain.handle('app:info', () => require('./app-info').getAppInfo());
```

**1.4 `stores/help-store.js`**
```js
// 状态: { visible: false, activeTab: 'about' }
// 方法: open(tab), close(), setTab(tab)
```

**1.5 `components/HelpDialog/`**
- `HelpDialog.vue` + `HelpDialog.scoped.css` + `index.js`
- 模态遮罩 + 内容容器 + 关闭按钮
- Props: `visible`, `title`；Events: `close`
- 使用 `<Teleport to="body">` 避免 z-index 问题

### Phase 2：关于面板

**2.1 `AboutContent.vue` + `AboutContent.scoped.css`**
- 调用 `window.electronAPI.getAppInfo()` 获取数据
- 展示：应用名、版本号、描述、Electron/Chrome/Node 版本、作者、GitHub 链接
- GitHub 链接点击 → `window.electronAPI.openExternal(url)`

**2.2 preload 新增 `openExternal`**
- INVOKE_CHANNELS 加 `'shell:open-external'`
- `main.js`: `ipcMain.handle('shell:open-external', (_, url) => shell.openExternal(url))`
- 安全校验：仅允许 `https://` 开头的 URL

### Phase 3：快捷键一览

**3.1 快捷键数据源**
- 在 `electron/app-menu.js` 或独立 `config/shortcuts.json` 中维护声明式列表
- 通过 IPC `app:shortcuts` 传给渲染进程

**3.2 `ShortcutsContent.vue` + `ShortcutsContent.scoped.css`**
- 按分组（会议、音频、视图、帮助）渲染表格
- 快捷键用 `<kbd>` 标签展示

### Phase 4：使用帮助文档

**4.1 帮助文档**
- 新建 `docs/user-guide.md`，Markdown 格式
- 内容：快速开始、音频配置、会议流程、字幕与翻译、摘要编辑、常见问题

**4.2 `HelpDocContent.vue` + `HelpDocContent.scoped.css`**
- 通过 IPC 读取 `user-guide.md` 原文
- 用轻量 Markdown 渲染（`marked` 或 `markdown-it`，需加入依赖）
- 添加目录导航侧栏（从 headings 自动生成）

**4.3 安全：对 Markdown HTML 输出做 sanitize**
- 使用 `DOMPurify` 或 `marked` 内置 sanitizer 防止 XSS

### Phase 5：检查更新

**5.1 `electron/update-checker.js`**
```js
// checkForUpdate(currentVersion) → { hasUpdate, latest, url, releaseNotes }
// 请求 GitHub API: GET /repos/{owner}/{repo}/releases/latest
// 版本比较: semver.gt(latest, current)
```

**5.2 集成**
- 菜单点击 → 主进程调 `checkForUpdate()` → `dialog.showMessageBox()` 展示结果
- 有新版本时提供 "前往下载" 按钮 → `shell.openExternal(releaseUrl)`

### Phase 6：反馈问题

- 菜单点击 → 主进程直接 `shell.openExternal('https://github.com/kokomaya/Imok/issues/new')`
- 无需渲染进程参与

### Phase 7：菜单集成

**7.1 `app-menu.js` 扩展**
```js
{
  label: '帮助',
  submenu: [
    { label: '使用帮助',   accelerator: 'F1',              click: () => send('help-doc') },
    { label: '快捷键一览', accelerator: 'CmdOrCtrl+/',     click: () => send('help-shortcuts') },
    { type: 'separator' },
    { label: '检查更新…',                                   click: () => handleCheckUpdate() },
    { label: '反馈问题',                                    click: () => shell.openExternal(ISSUES_URL) },
    { type: 'separator' },
    { label: '关于 Imok',                                   click: () => send('help-about') },
  ],
}
```

**7.2 `App.vue` 扩展 `handleMenuAction`**
```js
case 'help-about':     helpStore.open('about');     break;
case 'help-shortcuts': helpStore.open('shortcuts'); break;
case 'help-doc':       helpStore.open('doc');       break;
```

---

## 四、新增 / 修改文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `electron/app-info.js` | 应用元数据 |
| 新建 | `electron/update-checker.js` | 版本检查 |
| 新建 | `stores/help-store.js` | 弹窗状态管理 |
| 新建 | `components/HelpDialog/HelpDialog.vue` | 通用模态弹窗 |
| 新建 | `components/HelpDialog/HelpDialog.scoped.css` | 弹窗样式 |
| 新建 | `components/HelpDialog/AboutContent.vue` | 关于页 |
| 新建 | `components/HelpDialog/AboutContent.scoped.css` | 关于页样式 |
| 新建 | `components/HelpDialog/ShortcutsContent.vue` | 快捷键页 |
| 新建 | `components/HelpDialog/ShortcutsContent.scoped.css` | 快捷键页样式 |
| 新建 | `components/HelpDialog/HelpDocContent.vue` | 帮助文档页 |
| 新建 | `components/HelpDialog/HelpDocContent.scoped.css` | 帮助文档页样式 |
| 新建 | `components/HelpDialog/index.js` | barrel export |
| 新建 | `docs/user-guide.md` | 用户帮助文档 |
| 修改 | `electron/app-menu.js` | 新增"帮助"菜单 |
| 修改 | `electron/main.js` | 注册 `app:info`, `shell:open-external` IPC |
| 修改 | `electron/preload.js` | 白名单 + API 暴露 |
| 修改 | `src/App.vue` | 引入 HelpDialog，扩展 handleMenuAction |
| 可选 | `package.json` | 添加 `marked` + `dompurify` 依赖（Phase 4） |

---

## 五、依赖影响

| 依赖 | 用途 | 阶段 | 备注 |
|------|------|------|------|
| `marked` | Markdown → HTML | Phase 4 | 轻量，~40KB |
| `dompurify` | HTML sanitize 防 XSS | Phase 4 | 安全必需 |

Phase 1-3, 5-6 无新依赖。

---

## 六、实施顺序建议

```
Phase 1 (基础设施) → Phase 2 (关于) → Phase 7 (菜单集成)
  → 此时可交付基本可用版本
  → Phase 3 (快捷键) → Phase 6 (反馈) → Phase 5 (更新检查) → Phase 4 (帮助文档)
```

Phase 4 最后做，因为需要额外依赖和编写文档内容。