# 闭麦表达助手 — 场景自定义 & 多候选表达

## 需求概述

闭麦表达助手需要支持：
1. **场景自定义**：用户可创建、编辑、删除、选择不同会议场景（如与国外专家交流、与同事交流、与客户交流等），每个场景有独立的描述，LLM 会根据场景调整语气和措辞
2. **场景全局持久化**：场景配置独立于工作区（会议），所有会议共享同一份场景库
3. **多候选表达**：用户可配置一次生成多少条候选英文表达（如 1~5 条），方便挑选最合适的

## 现状分析

| 层 | 现状 | 问题 |
|---|---|---|
| 后端 `config/scenes.json` | 已有 4 个预设场景 | ✅ 可复用 |
| 后端 `SceneManager` | 已有 CRUD + default 管理 | ✅ 可复用 |
| 后端 `PromptManager.render_expression()` | 已注入 `scene_description` | ✅ 可复用 |
| 前端 `expression-service.js` | **硬编码 system prompt，无场景注入** | ❌ 需改造 |
| 前端 `prompts/index.js` | 固定 prompt，无场景变量 | ❌ 需改造 |
| 前端 UI `MuteAssistPanel.vue` | **无场景选择器** | ❌ 需新增 |
| 前端持久化 | 无场景存储 | ❌ 需新增 IPC + 本地文件 |
| 多候选 | prompt 写死"不输出多个版本" | ❌ 需改造 |

## 实现计划

### Phase 1：前端场景存储 & 持久化

**目标**：场景数据在前端可 CRUD，持久化到本地文件（独立于会议目录）。

#### Step 1.1 — Electron 主进程：场景文件 IPC

在 `main.js` 中注册 IPC handler，读写 `config/scenes.json`：

- `ipcMain.handle('scenes:list')` → 读取并返回 scenes 数组
- `ipcMain.handle('scenes:save', scenes)` → 原子写入 scenes.json
- 文件路径：`config/scenes.json`（已存在，直接复用）

#### Step 1.2 — Preload 暴露 API

在 `preload.js` 中暴露：
- `window.electronAPI.listScenes()` → invoke `scenes:list`
- `window.electronAPI.saveScenes(scenes)` → invoke `scenes:save`

#### Step 1.3 — 前端场景 Store

新建 `frontend/src/stores/scene-store.js`：

```
state:
  scenes: Scene[]           // { id, name, description, isDefault }
  activeSceneId: string     // 当前选中的场景 ID

actions:
  load()                    // 从 IPC 加载
  save()                    // 保存到 IPC
  addScene(name, desc)      // 生成唯一 ID，追加
  updateScene(id, patch)    // 更新名称/描述
  removeScene(id)           // 删除（禁止删除最后一个）
  setActive(id)             // 切换当前场景
  getActiveScene()          // 返回当前场景对象
```

场景 Store 在 App 启动时自动 `load()`，变更后自动 `save()`。

---

### Phase 2：前端表达设置 Store

**目标**：管理「候选条数」等表达偏好设置，全局持久化。

#### Step 2.1 — 表达设置 Store

新建 `frontend/src/stores/expression-settings-store.js`：

```
state:
  candidateCount: number    // 候选表达条数，默认 1，范围 1~5

actions:
  load()                    // 从 electronAPI.getExpressionSettings() 加载
  save()                    // 保存到本地
  setCandidateCount(n)      // 设置候选条数
```

持久化方式：复用 Electron 的 `config/settings.json` 或独立 `config/expression_settings.json`。

#### Step 2.2 — Electron IPC

- `ipcMain.handle('expression-settings:get')` → 读取
- `ipcMain.handle('expression-settings:save', settings)` → 写入

---

### Phase 3：改造 Prompt & 表达服务

**目标**：让 expression-service 注入场景描述 + 支持多候选输出。

#### Step 3.1 — Prompt 模板化

修改 `frontend/src/prompts/index.js`，将 `EXPRESSION_SYSTEM_PROMPT` 改为函数：

```js
export function buildExpressionPrompt(sceneDescription, candidateCount) {
  return `你是会议中的英文表达助手，请将用户输入的中文转换为适合当前会议场景的英文说法。

当前会议场景：${sceneDescription}

要求：
- 保持原意准确
- 语气和措辞要符合当前会议场景
- 表达自然、简洁
- 如果输入是口语转写结果，自动修正明显口误或 ASR 噪声后再输出
${candidateCount > 1
  ? `- 提供 ${candidateCount} 种不同的表达方式，用换行分隔，每行前标注序号（如 1. 2. 3.）`
  : '- 仅输出一种最佳表达，不添加解释'}
- 仅输出英文结果`;
}
```

#### Step 3.2 — expression-service 注入场景

修改 `expression-service.js` 的 `express()` 函数：
- 从 `sceneStore.getActiveScene()` 获取当前场景描述
- 从 `expressionSettingsStore` 获取候选条数
- 调用 `buildExpressionPrompt(desc, count)` 生成 system prompt
- 传入 LLM 调用

#### Step 3.3 — Store 解析多候选

修改 `mute-assist-store.js`：
- `outputText` 改为 `outputs: string[]`（多候选数组）
- `finishExpression()` 中解析 LLM 返回的编号列表，拆分为数组
- 保留向后兼容：`candidateCount === 1` 时 `outputs` 只有一个元素

---

### Phase 4：UI — 场景选择器 & 设置

**目标**：在闭麦表达面板中添加场景选择 + 候选条数设置 + 多候选展示。

#### Step 4.1 — 场景选择器（下拉 + 管理入口）

在 `MuteAssistPanel.vue` 的 header 区域添加：
- **场景下拉选择器**：显示所有场景名称，选中项高亮，切换即生效
- **「管理场景」按钮**：打开场景管理弹窗

#### Step 4.2 — 场景管理弹窗

新建 `SceneManager.vue` 组件（或内嵌 `MuteAssistPanel`）：
- 场景列表：每行显示名称 + 描述摘要 + 编辑/删除按钮
- 「新增场景」：输入名称 + 描述 textarea → 保存
- 「编辑场景」：修改名称/描述 → 保存
- 「删除场景」：确认后删除（至少保留一个）
- 所有操作实时保存到 scenes.json

#### Step 4.3 — 候选条数设置

在场景管理弹窗或面板 header 中：
- 下拉/数字选择器：1~5 条
- 变更后立即持久化

#### Step 4.4 — 多候选展示

修改 `MuteAssistPanel.vue` 输出区域：
- 候选数 = 1 时：保持现有单文本展示
- 候选数 > 1 时：按编号展示多条，每条有独立的「复制」按钮
- 历史记录也存储多候选

---

### Phase 5：收尾 & 测试

#### Step 5.1 — 菜单同步

`app-menu.js` 视图菜单中，已有「闭麦表达助手」项，无需改动。

#### Step 5.2 — 前端测试

- 场景 CRUD 验证：新增/编辑/删除/切换
- 持久化验证：重启后场景和设置恢复
- 多候选解析：1 条 / 3 条 / 5 条分别验证
- 边界：删除最后一个场景被阻止、描述为空时的处理

#### Step 5.3 — 后端测试

- 跑通现有 464 测试，确保无回归

---

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `frontend/src/stores/scene-store.js` | 新建 | 场景 CRUD + 持久化 |
| `frontend/src/stores/expression-settings-store.js` | 新建 | 候选条数等表达偏好 |
| `frontend/src/prompts/index.js` | 修改 | EXPRESSION prompt 模板化 |
| `frontend/src/services/expression-service.js` | 修改 | 注入场景 + 候选条数 |
| `frontend/src/stores/mute-assist-store.js` | 修改 | 支持多候选 outputs |
| `frontend/src/components/MuteAssistPanel/MuteAssistPanel.vue` | 修改 | 场景选择器 + 多候选 UI |
| `frontend/src/components/MuteAssistPanel/SceneManager.vue` | 新建 | 场景管理弹窗 |
| `frontend/src/components/MuteAssistPanel/MuteAssistPanel.scoped.css` | 修改 | 新 UI 样式 |
| `frontend/electron/main.js` | 修改 | scenes + settings IPC handler |
| `frontend/electron/preload.js` | 修改 | 暴露 scenes/settings API |
| `config/scenes.json` | 保留 | 预设场景不变，用户可增删 |

## 不涉及的改动

- 后端 Python 代码：本次不改动，前端直接调 LLM，场景管理纯前端
- 会议保存/工作区：场景独立于工作区，不纳入 workspaceStore
