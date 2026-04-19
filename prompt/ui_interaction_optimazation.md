# UI 交互逻辑审查

## 一、完整交互链路梳理

### 1. 会议生命周期

```
[开始会议] → getSourceType() → sendControl('switch_source') → sendControl('start')
  → python:status {state:'running'} → meetingActive=true → 按钮变为「⏹ 停止」
  → python:transcription 推送开始

[停止会议] → sendControl('stop')
  → python:status {state:'stopped'} → meetingActive=false → 按钮变回「▶ 开始会议」
```

### 2. 音频源切换

```
[🔊 系统音频] / [🎤 麦克风] checkbox toggle
  → onAudioToggle()
  → 如果会议未运行 → 无操作（仅记录状态）
  → 如果会议运行中 → sendControl('switch_source', { source })
  → 如果两个都关 → showError + 强制恢复系统音频
```

### 3. 实时字幕流

```
python:transcription 事件
  → transcriptions.push({id, text, language, speaker, timestamp})
  → watch(transcriptions) → scrollToBottom()
  → 模板渲染: [timestamp] [Speaker_X] [language] text
```

### 4. 悬浮字幕

```
[🎯] → openOverlay() → electronAPI.openOverlay()
  → main.js 创建 overlay BrowserWindow (#/overlay 路由)
  → SubtitleOverlay 组件挂载 → ipcBridge.init()
  → python:transcription → subtitleStore.addTranscription() → llmClient.translateEntry()
```

### 5. 闭麦表达助手

```
[💬] 或 Ctrl+Shift+M → muteAssistStore.toggleVisible()
  → MuteAssistPanel 显示/隐藏
  → 输入文本 + Enter → handleSubmit()
    → expressionService.express(inputText)
      → muteAssistStore.startExpression() → llmChat() → appendOutput() → finishExpression()
  → 复制按钮 → clipboard.writeText() → markCopied()
```

### 6. 会议摘要（实时模式）

```
[📝] → summaryStore.toggleVisible() → SummaryPanel 显示/隐藏

自动推送链路:
  python:segment-summary → summaryStore.addSegmentSummary()
  python:global-summary → summaryStore.updateGlobalSummary()

手动触发:
  [▶ 生成段落摘要] → sendControl('trigger_segment_summary')
  [▶ 生成全局总结] → sendControl('trigger_global_summary')

间隔设置:
  ⏱ select 变更 → sendControl('set_summary_interval', { interval_s })
```

### 7. 会议历史

```
[📂] → toggleHistory() → historyVisible toggle
  → 如果打开 → refreshMeetingList() → electronAPI.listMeetings()
  → 显示会议列表

[点击某条会议] → loadMeeting(meetingId)
  → checkUnsavedSummary()
    → 如果 isDirty + reviewMeetingId → confirm('是否保存？')
      → 确定 → saveMeetingSummaries() → markSaved()
      → 取消 → 继续（丢弃修改）
  → electronAPI.loadMeeting(meetingId)
  → 填充 transcriptions + summaryStore
  → setReviewData(trans, meetingId)
  → loadedMeetingId = meetingId

[🗑 删除] → deleteMeeting(meetingId)
  → confirm('确定删除？')
  → electronAPI.deleteMeeting(meetingId) → fs.rmSync
  → 从 historyMeetings 列表移除
  → 如果删除当前加载的 → 清空 loadedMeetingId + transcriptions + summaryStore
```

### 8. 会议摘要（回看模式）

```
回看条件: loadedMeetingId !== null → summaryStore.state.reviewMode = true

[▶ 生成段落摘要] → generateReviewSummary()
  → 取 reviewTranscriptions → llmChat(SUMMARY_SYSTEM_PROMPT) → addSegmentSummary()

[▶ 生成全局总结] → generateReviewGlobalSummary()
  → 如果无段落摘要 → 先 generateReviewSummary()
  → 取 segments rawText → llmChat(MERGE_SYSTEM_PROMPT) → updateGlobalSummary()

[💾 保存] → saveSummaries()
  → getSummariesForSave() → electronAPI.saveMeetingSummaries(reviewMeetingId, data)
  → markSaved()
```

### 9. 返回实时

```
[返回实时] → backToLive()
  → checkUnsavedSummary() (同上)
  → loadedMeetingId = null
  → transcriptions = []
  → summaryStore.clearAll()
```

### 10. 窗口关闭保护

```
window.beforeunload
  → 如果 isDirty + reviewMeetingId → 阻止关闭，浏览器弹出默认确认
```

---

## 二、发现的问题

### 🔴 P0 — 严重问题（阻断交互或丢失数据）

#### 问题 1：实时模式停止会议后，摘要数据无保存入口

**场景：** 用户在实时模式下开会 → 自动生成了段落摘要和全局总结 → 停止会议 → 摘要数据仅在内存中，无法保存。

**原因：** 保存按钮 `v-if="summaryStore.state.reviewMeetingId"` — 只在回看模式下显示。实时模式下 `reviewMeetingId` 为空，保存按钮不存在。

**影响：** 用户看到了摘要但无法手动保存；如果切换到其他会议或关闭窗口，摘要丢失。

**建议：** 实时模式停止会议后，应自动将摘要保存到当前 meeting，或提供保存按钮。

---

#### 问题 2：实时模式下新会议覆盖旧摘要，无清空提示

**场景：** 用户停止会议 A → 摘要面板仍显示 A 的数据 → 开始新会议 B → B 的 segment-summary 推送追加到 A 的列表中，混合在一起。

**原因：** `startMeeting()` 没有调用 `summaryStore.clearAll()` 和 `transcriptions.value = []`。

**影响：** 两次会议的摘要和字幕混在一起，数据错乱。

---

#### 问题 3：checkUnsavedSummary 的 confirm 语义矛盾

**场景：** 弹窗提示 "点击确定保存，点击取消放弃修改"。用户点取消（放弃修改）→ 函数返回 `true` → 继续后续操作。

**实际问题：** 这意味着"取消"＝"放弃修改并继续"，但 `window.confirm` 的取消按钮在用户心智模型中通常表示"我不想继续操作"。用户想取消整个导航，却被理解为放弃修改。

**建议：** 改为三态对话框（保存 / 不保存 / 取消），或将 confirm 改为"有未保存修改，确定离开？"语义。

---

### 🟡 P1 — 体验问题（交互不顺畅）

#### 问题 4：删除非当前加载的会议后，历史列表不刷新

**场景：** 用户正在回看 A → 删除 B → B 从 `historyMeetings` 数组移除了（UI 正确）→ 但如果 B 的删除失败（result.ok=false），没有任何错误提示。

**建议：** 删除失败时 `showError(result.error)`。

---

#### 问题 5：loadMeeting 失败时无用户反馈

**场景：** 用户点击历史会议 → `electronAPI.loadMeeting()` 返回 `{ ok: false }` → `console.error` + `return` → 用户看到历史面板关闭了但什么都没发生。

**原因：** `loadMeeting()` 中 `!result.ok` 分支只有 `console.error`，没有 `showError()`。

**建议：** 加 `showError('加载会议失败：' + result.error)`。

---

#### 问题 6：回看提示条显示原始 meetingId（UUID），用户不可读

**场景：** `📖 正在回看历史会议: mtg_1713456789_abc123` — 这个 ID 对用户无意义。

**建议：** 显示会议开始时间或序号，而非内部 ID。

---

#### 问题 7：历史面板与摘要面板/闭麦面板重叠

**场景：** 用户同时打开摘要面板 + 历史面板 + 闭麦面板 → 三者都在 `.content` 区域上方或内部渲染，布局挤压。

**原因：** 历史面板是 `border-bottom` 的展开区域，摘要和闭麦面板是 `.content` 内的组件，没有互斥或优先级。

**建议：** 打开历史面板时自动收起摘要/闭麦面板，或用 Tab/侧边栏设计替代堆叠。

---

#### 问题 8：音频源开关在会议未运行时可以改，但无视觉提示说明"将在下次会议生效"

**场景：** 用户在会议未运行时关闭系统音频 → 没有任何反馈 → 开始会议后才知道只有麦克风在录。

**建议：** 非会议状态下切换音频源时，给一个小提示"将在下次开始会议时生效"。

---

#### 问题 9：triggerSegmentSummary 在实时模式下状态反馈不准确

**场景：** 实时模式下点击「▶ 生成段落摘要」→ `sendControl('trigger_segment_summary')` → 按钮立即恢复（triggeringSegment=false），但后端可能还在处理 → 用户以为没生效又点一次。

**原因：** 实时模式下 `triggeringSegment` 在 `sendControl` await 返回后立刻设回 `false`，而 `sendControl` 只是发了个消息，后端真正完成是通过 `python:segment-summary` 事件异步通知的。

**建议：** 实时模式下，按钮的 loading 状态应保持到 `python:segment-summary` 事件到达。

---

#### 问题 10：回看模式下重复点击「生成段落摘要」会追加而非替换

**场景：** 用户在回看模式下点了三次「生成段落摘要」→ segments 里出现三份相同内容的摘要。

**原因：** `confirmOverwrite` 只在 `hasSummaryContent=true` 时弹出，但 confirm 后执行的是 `addSegmentSummary`（追加），不是替换。

**建议：** 回看模式下重新生成应先 `clearAll` segments 再添加。

---

#### 问题 11：SubtitleOverlay 与主窗口数据独立，可能出现不一致

**场景：** 主窗口停止会议 → overlay 窗口仍然显示最后几条字幕，没有任何"已停止"状态反馈。overlay 有自己独立的 `subtitleStore`，不会因主窗口操作而清空。

**建议：** 在 overlay 中监听 `python:status` 的 `stopped` 状态，显示"会议已结束"并标灰字幕。

---

### 🟢 P2 — 优化建议

#### 问题 12：expressionService 中存在死代码

`readSSEStream` 和 `readJSONResponse` 已不被调用（当前使用 `llmChat` IPC 代理），建议移除。

---

#### 问题 13：beforeunload 保护仅限摘要编辑，不保护实时会议

用户在会议录制中直接关闭窗口，没有任何确认提示，会议数据可能不完整。

**建议：** `meetingActive.value === true` 时也应触发 `beforeunload` 警告。

---

#### 问题 14：历史会议没有搜索/筛选能力

随着会议记录增多，长列表中找到目标会议困难。建议加日期筛选或关键词搜索。
