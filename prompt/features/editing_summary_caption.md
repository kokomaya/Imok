# 字幕 & 摘要手动编辑功能计划

## 1. 目标

允许用户在**回看模式（reviewMode）**下手动编辑：
- **字幕**：修改 ASR 转写原文（`original`）和翻译（`translation`）
- **段落摘要**：修改各字段（`topics`、`conclusions`、`actionItems`、`rawText`）
- **全局摘要**：修改 `rawText`
- **Action Items**：修改 `description`、`assignee`、`deadline`

编辑后自动标记脏状态，与现有保存按钮联动（`💾` 按钮高亮，Ctrl+S 可保存）。

> 约束：实时录制模式下不开放编辑，避免与 ASR/LLM 流式推送冲突。

---

## 2. 现状分析

| 层级 | 现状 | 差距 |
|------|------|------|
| **字幕 Store** (`subtitle-store.js`) | 只有 `addTranscription` / `updateTranslation`，无原文编辑 API | 需要 `editOriginal(id, text)` / `editTranslation(id, text)` |
| **摘要 Store** (`summary-store.js`) | 只有 `addSegmentSummary` / `updateGlobalSummary`，无字段级编辑 API | 需要各字段的 mutation 方法 |
| **Workspace Store** (`workspace-store.js`) | `markTranscriptionEdited()` 已存在但从未被调用 | 编辑时调用即可 |
| **SubtitleOverlay** | 纯 `{{ }}` 只读渲染 | 需要可切换的编辑态 |
| **SummaryPanel** | 4 个 tab 全部只读 | 需要内联编辑组件 |
| **保存流程** | 只保存 `summaries.json`，不保存转写编辑 | 需要新增 `meeting:save-transcriptions` IPC |
| **后端 MeetingStore** | `transcriptions.jsonl` 仅 append | 需要 `save_transcriptions()` 全量覆写方法 |

---

## 3. 架构设计（SOLID）

### 3.1 新增组件/模块一览

```
frontend/src/
├── components/
│   ├── common/
│   │   └── InlineEdit.vue          # [NEW] 通用内联编辑组件（SRP）
│   ├── SubtitleOverlay/
│   │   └── SubtitleEntry.vue       # [NEW] 单条字幕组件（含只读/编辑态切换）
│   └── SummaryPanel/
│       ├── EditableField.vue       # [NEW] 单字段编辑器（text/list 两种模式）
│       └── SummaryPanel.vue        # [MODIFY] 集成编辑态
├── composables/
│   └── useInlineEdit.js            # [NEW] 编辑态逻辑复用（OCP）
├── stores/
│   ├── subtitle-store.js           # [MODIFY] 添加编辑 API
│   ├── summary-store.js            # [MODIFY] 添加编辑 API
│   └── workspace-store.js          # [MODIFY] 添加转写脏状态的内容哈希
└── services/
    └── ipc-bridge.js               # [UNCHANGED] 无需修改

frontend/electron/
├── main.js                         # [MODIFY] 新增 meeting:save-transcriptions handler
└── preload.js                      # [MODIFY] 暴露 saveTranscriptions API

backend/storage/
└── meeting_store.py                # [MODIFY] 新增 save_transcriptions() 方法
```

### 3.2 设计原则映射

| 原则 | 应用 |
|------|------|
| **SRP** | `InlineEdit.vue` 只管 UI 交互（聚焦/失焦/按键）；Store 只管状态变更；Save 只管持久化 |
| **OCP** | `useInlineEdit` composable 封装通用逻辑，新增字段编辑无需修改已有组件 |
| **LSP** | `EditableField.vue` 通过 `mode` prop（`text` / `list` / `textarea`）适配不同字段类型 |
| **ISP** | 编辑 API 按粒度拆分（`editSegmentTopic` / `editSegmentConclusion` / ...），调用方只依赖需要的方法 |
| **DIP** | 组件通过 `emit` 上报变更，不直接调用 Store；父组件负责调度 Store mutation |

---

## 4. 详细实现步骤

### Phase 1: 基础设施 — Store 编辑 API + 脏状态

#### 4.1 `subtitle-store.js` — 添加编辑方法

```js
// 新增方法
function editOriginal(id, newText) {
  const entry = state.entries.find(e => e.id === id);
  if (!entry || entry.original === newText) return;
  entry.original = newText;
}

function editTranslation(id, newText) {
  const entry = state.entries.find(e => e.id === id);
  if (!entry || entry.translation === newText) return;
  entry.translation = newText;
  entry.translationStatus = 'done'; // 手动编辑视为完成
}
```

导出时追加到 `subtitleStore` 对象。

#### 4.2 `summary-store.js` — 添加编辑方法

```js
// 段落摘要编辑
function editSegmentField(segmentId, field, value) {
  const seg = state.segments.find(s => s.id === segmentId);
  if (!seg) return;
  seg[field] = value;  // field: 'topics' | 'conclusions' | 'actionItems' | 'rawText'
}

// 全局摘要编辑
function editGlobalRawText(newText) {
  if (!state.globalSummary) return;
  state.globalSummary.rawText = newText;
}

// Action Item 编辑
function editActionItem(index, field, value) {
  const item = state.globalSummary?.actionItems?.[index];
  if (!item) return;
  item[field] = value;  // field: 'description' | 'assignee' | 'deadline' | 'status'
}
```

> `_contentHash()` 已基于 `rawText` 计算，编辑后 `isDirty` 会自动变为 `true` — 无需额外处理。

#### 4.3 `workspace-store.js` — 转写编辑脏检测

现有 `transcriptionEdited` 是布尔标记，保存后重置。已满足需求，编辑字幕时调用 `markTranscriptionEdited()` 即可。

---

### Phase 2: 通用 UI 组件

#### 4.4 `InlineEdit.vue` — 通用内联编辑组件

**Props**:
| Prop | Type | 说明 |
|------|------|------|
| `modelValue` | `String` | 当前文本 |
| `tag` | `String` | 只读态渲染标签（`span` / `p` / `div`），默认 `span` |
| `multiline` | `Boolean` | 是否多行（`<textarea>` vs `<input>`） |
| `disabled` | `Boolean` | 禁止编辑 |
| `placeholder` | `String` | 空值占位文本 |

**Events**: `update:modelValue`

**行为**:
- 只读态：显示文本，hover 时显示铅笔图标或虚线下划线提示可编辑
- 双击或点击铅笔图标 → 进入编辑态，自动聚焦 + 全选
- `Enter`（单行）/ `Ctrl+Enter`（多行）→ 提交，触发 `update:modelValue`
- `Escape` → 取消编辑，恢复原值
- 失焦 → 提交

#### 4.5 `EditableField.vue` — 列表型字段编辑器

用于 `topics[]`、`conclusions[]`、`actionItems[]` 等数组字段。

**Props**:
| Prop | Type | 说明 |
|------|------|------|
| `items` | `String[]` | 列表项 |
| `disabled` | `Boolean` | 禁止编辑 |
| `addLabel` | `String` | 添加按钮文案，如 "添加主题" |

**Events**: `update:items`

**行为**:
- 每一项用 `InlineEdit` 渲染
- 尾部 "+ 添加" 按钮
- 每项前有 "×" 删除按钮（hover 可见）
- 通过 `update:items` 事件传出完整数组副本

#### 4.6 `useInlineEdit.js` — Composable

```js
export function useInlineEdit(store, markDirty) {
  // editing: ref<{ id, field } | null>
  // startEdit(id, field) / commitEdit(id, field, value) / cancelEdit()
  // 内部在 commitEdit 时调用 store 的对应 mutation 并触发 markDirty()
}
```

---

### Phase 3: 字幕编辑集成

#### 4.7 `SubtitleEntry.vue` — 单条字幕组件

从 `SubtitleOverlay.vue` 的 `<TransitionGroup>` 内部模板提取为独立组件。

**Props**: `entry: SubtitleEntry`, `editable: Boolean`

**Template 结构**:
```html
<div class="subtitle-entry">
  <InlineEdit v-if="editable" :modelValue="entry.original"
    @update:modelValue="$emit('edit-original', entry.id, $event)" />
  <span v-else>{{ entry.original }}</span>

  <InlineEdit v-if="editable && entry.translation" :modelValue="entry.translation"
    @update:modelValue="$emit('edit-translation', entry.id, $event)" />
  <span v-else>{{ entry.translation }}</span>
</div>
```

#### 4.8 `SubtitleOverlay.vue` — 集成编辑态

- 添加 computed `editable`：`summaryStore.state.reviewMode && !subtitleStore.state.paused`
- 将内联模板替换为 `<SubtitleEntry :entry="entry" :editable="editable" />`
- 监听 `edit-original` / `edit-translation` 事件，调用 Store mutation + `workspaceStore.markTranscriptionEdited()`

---

### Phase 4: 摘要编辑集成

#### 4.9 `SummaryPanel.vue` — 各 Tab 添加编辑能力

**Segment Tab**:
- `topics` → `<EditableField :items="currentTopics" :disabled="!editable" @update:items="..." />`
- `conclusions` → 同上

**Global Tab**:
- `rawText` → `<InlineEdit :modelValue="globalRawText" multiline :disabled="!editable" @update:modelValue="..." />`

**Actions Tab**:
- 每个 ActionItem 的 `description` / `assignee` / `deadline` → `<InlineEdit />`
- `status` → `<select>` 下拉

**Timeline Tab**:
- 每个 segment 的 `topics` / `conclusions` / `actionItems` → `<EditableField />`

**编辑权限**: computed `editable = summaryStore.state.reviewMode`（实时模式不可编辑）。

---

### Phase 5: 保存流程扩展

#### 4.10 后端 `meeting_store.py` — 新增方法

```python
def save_transcriptions(self, meeting_id: str, entries: list[TranscriptionEntry]) -> None:
    """全量覆写 transcriptions.jsonl（线程安全）。"""
    path = self._meeting_dir(meeting_id) / _TRANSCRIPTIONS_FILE
    tmp = path.with_suffix('.jsonl.tmp')
    with self._lock:
        with open(tmp, 'w', encoding='utf-8') as f:
            for e in entries:
                f.write(json.dumps(e.to_dict(), ensure_ascii=False) + '\n')
        tmp.replace(path)
```

#### 4.11 Electron IPC — 新增 `meeting:save-transcriptions`

**`preload.js`**:
```js
saveTranscriptions: (meetingId, entries) => {
  return ipcRenderer.invoke('meeting:save-transcriptions', meetingId, entries);
},
```

**`main.js`**:
```js
ipcMain.handle('meeting:save-transcriptions', (_event, meetingId, entries) => {
  // 校验 meetingId, entries
  // 原子写入 transcriptions.jsonl（.tmp + rename）
  const txPath = path.join(dir, 'transcriptions.jsonl');
  const tmpPath = txPath + '.tmp';
  const lines = entries.map(e => JSON.stringify(e)).join('\n') + '\n';
  fs.writeFileSync(tmpPath, lines, 'utf-8');
  fs.renameSync(tmpPath, txPath);
  return { ok: true };
});
```

#### 4.12 `subtitle-store.js` — 添加 `getTranscriptionsForSave()`

```js
function getTranscriptionsForSave() {
  return state.entries.map(e => ({
    text: e.original,
    timestamp: e.timestamp,
    language: e.language,
  }));
}
```

#### 4.13 `App.vue` `saveWorkspace()` — 扩展保存逻辑

```js
async function saveWorkspace() {
  // ... 现有摘要保存逻辑不变 ...

  // 新增：保存编辑后的转写
  if (workspaceStore.state.transcriptionEdited && window.electronAPI?.saveTranscriptions) {
    const txData = subtitleStore.getTranscriptionsForSave();
    const result = await window.electronAPI.saveTranscriptions(meetingId, txData);
    if (!result.ok) {
      showError('字幕保存失败：' + (result.error || '未知错误'));
      return;
    }
  }

  workspaceStore.markAllSaved();
}
```

---

## 5. 实现顺序 & 依赖关系

```
Phase 1 (Store API)
  ├── 4.1 subtitle-store 编辑方法
  ├── 4.2 summary-store 编辑方法
  └── 4.3 workspace-store 联动（已就绪）
       │
Phase 2 (通用组件)
  ├── 4.4 InlineEdit.vue
  ├── 4.5 EditableField.vue
  └── 4.6 useInlineEdit.js
       │
Phase 3 (字幕编辑)          Phase 4 (摘要编辑)
  ├── 4.7 SubtitleEntry.vue   ├── 4.9 SummaryPanel 改造
  └── 4.8 SubtitleOverlay改造 │
       │                      │
Phase 5 (保存扩展) ───────────┘
  ├── 4.10 backend save_transcriptions
  ├── 4.11 IPC 新通道
  ├── 4.12 subtitle-store getForSave
  └── 4.13 App.vue saveWorkspace 扩展
```

Phase 1–2 无外部依赖，可并行。Phase 3/4 依赖 Phase 2。Phase 5 依赖 Phase 1。

---

## 6. 不影响现有功能的保障

1. **编辑态仅在 `reviewMode` 下激活** — 实时录制完全不受影响
2. **Store 新方法是纯追加** — 不修改已有方法的签名或行为
3. **`isDirty` / `_contentHash` 天然兼容** — 编辑改变 `rawText` 等字段后哈希自动变化
4. **保存流程向后兼容** — 新增的转写保存逻辑有 `transcriptionEdited` 守卫，未编辑时不触发
5. **InlineEdit 默认 `disabled`** — 需显式传入 `editable` 才启用，不会意外激活

---

## 7. 样式约定

- 编辑态用 `1px dashed var(--border-color)` 虚线框包裹，区分只读态
- Hover 时显示 `✏️` 图标（通过 CSS `::after` pseudo-element）
- 编辑中 input/textarea 使用与现有面板一致的 `var(--bg-secondary)` 背景
- 添加/删除按钮使用 `opacity: 0 → 1` hover 动画，保持界面简洁