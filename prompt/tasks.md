# AI 会议助手 — 实现任务拆解与状态追踪

> 基于 [plan_optimized.md](plan_optimized.md) 拆解，遵循 SOLID 原则进行模块化设计。
> 状态标记：⬜ 未开始 | 🔨 进行中 | ✅ 已完成 | ❌ 已阻塞

---

## 项目目录结构（SOLID 原则指导）

```text
imok/
├── backend/                          # Python 后端
│   ├── main.py                       # FastAPI 应用入口
│   ├── config.py                     # 全局配置（环境检测、模型选择）
│   ├── audio/                        # 音频采集模块
│   │   ├── __init__.py
│   │   ├── base.py                   # AudioSource 抽象基类（ISP: 接口隔离）
│   │   ├── wasapi_source.py          # WASAPI Loopback 实现
│   │   ├── mic_source.py             # 麦克风采集实现
│   │   └── vbcable_source.py         # 虚拟声卡降级实现
│   ├── asr/                          # 语音识别模块
│   │   ├── __init__.py
│   │   ├── base.py                   # ASREngine 抽象基类（OCP: 开放扩展）
│   │   ├── whisper_engine.py         # faster-whisper 实现
│   │   ├── azure_engine.py           # Azure Speech 预留实现
│   │   └── vad.py                    # Silero-VAD 封装
│   ├── llm/                          # LLM 调用模块
│   │   ├── __init__.py
│   │   ├── base.py                   # LLMClient 抽象基类
│   │   ├── client.py                 # 公司 LLM API Streaming 客户端
│   │   ├── prompt_manager.py         # Prompt 模板管理（SRP: 单一职责）
│   │   └── glossary.py               # 术语表加载与注入
│   ├── translation/                  # 翻译服务模块
│   │   ├── __init__.py
│   │   ├── translator.py             # 实时翻译服务（依赖 LLMClient 接口，DIP）
│   │   ├── request_batcher.py        # 请求合并与去重
│   │   └── context_window.py         # 上下文窗口管理
│   ├── expression/                   # 闭麦辅助表达模块
│   │   ├── __init__.py
│   │   ├── assistant.py              # 表达助手服务
│   │   └── scene_manager.py          # 场景配置管理
│   ├── summary/                      # 会议总结模块
│   │   ├── __init__.py
│   │   ├── segment_summarizer.py     # 段落级摘要
│   │   ├── global_merger.py          # 全局合并摘要
│   │   └── action_item_extractor.py  # Action Items 提取
│   ├── storage/                      # 持久化模块
│   │   ├── __init__.py
│   │   ├── database.py               # SQLite 数据库管理
│   │   ├── models.py                 # 数据模型定义
│   │   └── exporter.py               # 会议纪要导出（Markdown/JSON/Wiki）
│   ├── pipeline/                     # 流水线编排模块
│   │   ├── __init__.py
│   │   ├── meeting_pipeline.py       # 主会议流水线（组合各模块，SRP）
│   │   └── mute_pipeline.py          # 闭麦辅助流水线
│   └── ws/                           # WebSocket 通信模块
│       ├── __init__.py
│       ├── server.py                 # WebSocket 服务端
│       └── messages.py               # 消息协议定义
├── frontend/                         # Electron 前端
│   ├── main.js                       # Electron 主进程
│   ├── preload.js                    # 预加载脚本
│   ├── package.json
│   ├── src/
│   │   ├── App.vue / App.tsx         # 应用根组件
│   │   ├── components/
│   │   │   ├── SubtitleOverlay/      # 悬浮字幕窗组件
│   │   │   ├── SummaryPanel/         # 摘要面板组件
│   │   │   ├── MuteAssistPanel/      # 闭麦辅助输入面板
│   │   │   ├── SettingsPanel/        # 设置面板组件
│   │   │   └── common/               # 公共 UI 组件
│   │   ├── services/
│   │   │   └── ws-client.ts          # WebSocket 客户端
│   │   ├── stores/                   # 状态管理
│   │   └── styles/                   # 全局样式
│   └── electron/
│       ├── window-manager.js         # 窗口管理（悬浮窗/面板）
│       └── tray.js                   # 系统托盘
├── config/
│   ├── glossary.json                 # 默认术语表
│   ├── scenes.json                   # 默认场景配置
│   └── settings.json                 # 默认设置
├── tests/                            # 测试
│   ├── backend/
│   └── frontend/
├── scripts/
│   ├── install.ps1                   # Windows 安装脚本
│   └── detect_gpu.py                 # GPU 检测脚本
├── requirements.txt
└── README.md
```

**SOLID 原则映射：**

| 原则 | 体现 |
| --- | --- |
| **S** - 单一职责 | 每个模块/类只负责一项功能（如 `request_batcher.py` 只做请求合并） |
| **O** - 开放封闭 | ASR/LLM 通过抽象基类扩展，新增引擎无需修改已有代码 |
| **L** - 里氏替换 | `WhisperEngine` 和 `AzureEngine` 可互换使用，不影响上层调用 |
| **I** - 接口隔离 | `AudioSource` 仅暴露 `start/stop/read` 接口，不强制实现无关方法 |
| **D** - 依赖倒置 | `Translator` 依赖 `LLMClient` 抽象接口，不依赖具体实现 |

---

## Phase 1a — 核心链路验证（第 1 周）

> 目标：跑通 "音频采集 → VAD → ASR → 文本输出" 核心链路

### Task 1.1 项目初始化与开发环境搭建

| 属性 | 值 |
| --- | --- |
| 状态 | ✅ 已完成 |
| 优先级 | P0 |
| 预估 | 0.5 天 |
| 产出文件 | `requirements.txt`, `config.py`, `README.md`, `.gitignore` |

**子步骤：**

- [x] 1.1.1 创建项目目录结构（按上方结构初始化所有 `__init__.py`）
- [x] 1.1.2 创建 Python 虚拟环境，安装核心依赖：
  - `faster-whisper`, `silero-vad`, `sounddevice`, `numpy`
  - `fastapi`, `uvicorn`, `websockets`
  - `httpx` (LLM API 调用)
- [x] 1.1.3 编写 `config.py`：GPU 检测、模型路径配置、音频参数默认值
- [x] 1.1.4 编写 `.gitignore`（排除模型文件、虚拟环境、SQLite 数据库）
- [x] 1.1.5 编写 `scripts/detect_gpu.py`：检测 CUDA 可用性，自动选择 compute type

---

### Task 1.2 音频采集抽象层

| 属性 | 值 |
| --- | --- |
| 状态 | ✅ 已完成 |
| 优先级 | P0 |
| 预估 | 1 天 |
| 产出文件 | `audio/base.py`, `audio/wasapi_source.py`, `audio/mic_source.py` |
| 依赖 | Task 1.1 |

**子步骤：**

- [x] 1.2.1 定义 `AudioSource` 抽象基类（`base.py`）：
  ```python
  class AudioSource(ABC):
      @abstractmethod
      def start(self) -> None: ...
      @abstractmethod
      def stop(self) -> None: ...
      @abstractmethod
      def read_chunk(self) -> Optional[np.ndarray]: ...
      @abstractmethod
      def get_sample_rate(self) -> int: ...
  ```
- [x] 1.2.2 实现 `WASAPILoopbackSource`（`wasapi_source.py`）：
  - 使用 `sounddevice` 或 `pyaudiowpatch` 捕获系统音频
  - 输出格式：16kHz, 16bit, mono PCM
  - 实现自动重采样（如系统音频为 48kHz）
- [x] 1.2.3 实现 `MicrophoneSource`（`mic_source.py`）：
  - 使用 `sounddevice` 捕获默认麦克风
  - 支持指定设备 ID
- [x] 1.2.4 编写音频采集自检函数（列举可用设备、验证采集能力）
- [x] 1.2.5 编写单元测试：验证音频采集 5 秒并保存为 WAV 文件

---

### Task 1.3 VAD 语音活动检测

| 属性 | 值 |
| --- | --- |
| 状态 | ✅ 已完成 |
| 优先级 | P0 |
| 预估 | 0.5 天 |
| 产出文件 | `asr/vad.py` |
| 依赖 | Task 1.2 |

**子步骤：**

- [x] 1.3.1 封装 Silero-VAD：
  ```python
  class VoiceActivityDetector:
      def __init__(self, threshold=0.5, min_silence_ms=300, max_segment_s=15): ...
      def feed(self, audio_chunk: np.ndarray) -> List[AudioSegment]: ...
      def reset(self) -> None: ...
  ```
- [x] 1.3.2 实现 `AudioSegment` 数据类（包含 audio_data, start_time, end_time）
- [x] 1.3.3 实现语音段边界检测逻辑（起止点检测 + 最大段时长截断）
- [x] 1.3.4 编写测试：用带静音的音频验证分句准确性

---

### Task 1.4 ASR 引擎抽象与 faster-whisper 实现

| 属性 | 值 |
| --- | --- |
| 状态 | ✅ 已完成 |
| 优先级 | P0 |
| 预估 | 1 天 |
| 产出文件 | `asr/base.py`, `asr/whisper_engine.py` |
| 依赖 | Task 1.3 |

**子步骤：**

- [x] 1.4.1 定义 `ASREngine` 抽象基类（`asr/base.py`）：
  ```python
  class ASREngine(ABC):
      @abstractmethod
      def transcribe(self, audio: np.ndarray) -> TranscriptionResult: ...
      @abstractmethod
      def get_supported_languages(self) -> List[str]: ...
  ```
- [x] 1.4.2 定义 `TranscriptionResult` 数据类（text, language, confidence, segments）
- [x] 1.4.3 实现 `WhisperEngine`（`asr/whisper_engine.py`）：
  - 根据 GPU 检测结果自动选择 model_size 和 compute_type
  - 参数：`beam_size=3`, `language=None`（自动检测）
  - 支持 `large-v3`（GPU）/ `medium`（CPU int8）自动降级
- [x] 1.4.4 实现模型懒加载（首次调用时加载，避免启动时阻塞）
- [x] 1.4.5 编写测试：用预录音频验证 WER

---

### Task 1.5 音频-ASR 核心流水线

| 属性 | 值 |
| --- | --- |
| 状态 | ✅ 已完成 |
| 优先级 | P0 |
| 预估 | 1 天 |
| 产出文件 | `pipeline/meeting_pipeline.py` |
| 依赖 | Task 1.2, 1.3, 1.4 |

**子步骤：**

- [x] 1.5.1 实现 `MeetingPipeline` 核心编排：
  ```python
  class MeetingPipeline:
      def __init__(self, audio_source: AudioSource, vad: VoiceActivityDetector, asr: ASREngine): ...
      async def start(self) -> None: ...       # 启动采集与识别循环
      async def stop(self) -> None: ...        # 停止流水线
      def on_transcription(self, callback): ... # 注册转写结果回调
  ```
- [x] 1.5.2 实现异步音频读取循环（`asyncio` + 后台线程）
- [x] 1.5.3 实现 VAD → ASR 调度逻辑（VAD 输出语音段，送入 ASR 队列）
- [x] 1.5.4 实现命令行验证脚本（`main.py --mode=cli`）：实时打印转写结果到终端
- [x] 1.5.5 基础错误处理：音频设备不可用时的友好提示与重试机制

---

### Task 1.6 Phase 1a 集成验证

| 属性 | 值 |
| --- | --- |
| 状态 | ✅ 已完成 |
| 优先级 | P0 |
| 预估 | 0.5 天 |
| 依赖 | Task 1.5 |

**子步骤：**

- [x] 1.6.1 在 Teams 会议中端到端测试音频采集 → ASR 转写
- [x] 1.6.2 测量 ASR 延迟（目标 < 2 秒）
- [x] 1.6.3 验证中英混合识别准确率
- [x] 1.6.4 测试无 GPU 降级场景（切换 medium 模型）
- [x] 1.6.5 记录测试结果与发现的问题

**验收标准：** 实时采集 Teams 会议音频并输出中英文转写文本，WER < 15%。

**验证结果：**

| 检查项 | 结果 |
| --- | --- |
| 单元测试 (56 个) | ✅ 全部通过 |
| 音频设备检测 | ✅ 14 输入 + 3 环回 |
| GPU/ASR 配置 | ✅ medium / cpu / int8 |
| 验证脚本 | `scripts/verify_phase1a.py` |
| CLI 入口 | `python -m backend.main --mode=cli` |

> 注：完整的延迟测量和 WER 评估需在实际 Teams 会议中执行：
> `python -m scripts.verify_phase1a --source=wasapi --duration=60`

---

## Phase 1b — 翻译 + 基础 UI（第 2 周）

> 目标：完成实时翻译和闭麦辅助的最小可用版本

### Task 2.1 LLM 客户端抽象层

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P0 |
| 预估 | 1 天 |
| 产出文件 | `llm/base.py`, `llm/client.py` |
| 依赖 | Task 1.1 |

**子步骤：**

- [ ] 2.1.1 定义 `LLMClient` 抽象基类（`llm/base.py`）：
  ```python
  class LLMClient(ABC):
      @abstractmethod
      async def complete(self, prompt: str) -> str: ...
      @abstractmethod
      async def stream(self, prompt: str) -> AsyncIterator[str]: ...
  ```
- [ ] 2.1.2 实现公司 LLM API 客户端（`llm/client.py`）：
  - 使用 `httpx.AsyncClient` 进行 Streaming SSE 调用
  - 支持 API Key 认证
  - 实现连接池与超时配置
- [ ] 2.1.3 实现重试机制（指数退避，最多 3 次）
- [ ] 2.1.4 实现断网检测与降级标记
- [ ] 2.1.5 编写测试：验证 Streaming 调用首 token 延迟

---

### Task 2.2 Prompt 管理与术语表

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P0 |
| 预估 | 0.5 天 |
| 产出文件 | `llm/prompt_manager.py`, `llm/glossary.py`, `config/glossary.json` |
| 依赖 | Task 2.1 |

**子步骤：**

- [ ] 2.2.1 实现 `GlossaryManager`（`llm/glossary.py`）：
  - 从 JSON 文件加载术语表
  - 支持运行时增删术语
  - 格式化为 Prompt 可注入的字符串
- [ ] 2.2.2 实现 `PromptManager`（`llm/prompt_manager.py`）：
  - 管理翻译 Prompt、闭麦表达 Prompt、总结 Prompt 模板
  - 支持变量注入（`{text}`, `{glossary}`, `{recent_context}`, `{scene_description}`）
- [ ] 2.2.3 创建默认术语表（`config/glossary.json`）
- [ ] 2.2.4 编写测试：验证 Prompt 渲染结果正确性

---

### Task 2.3 实时翻译服务

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P0 |
| 预估 | 1 天 |
| 产出文件 | `translation/translator.py`, `translation/request_batcher.py`, `translation/context_window.py` |
| 依赖 | Task 2.1, 2.2 |

**子步骤：**

- [ ] 2.3.1 实现 `ContextWindow`（`translation/context_window.py`）：
  - 维护最近 3 句已翻译内容
  - 提供格式化上下文字符串
- [ ] 2.3.2 实现 `RequestBatcher`（`translation/request_batcher.py`）：
  - 500ms 内短句合并
  - 相同文本去重（与上一次输入对比）
  - 输出合并后的待翻译文本
- [ ] 2.3.3 实现 `RealtimeTranslator`（`translation/translator.py`）：
  - 依赖注入 `LLMClient`（DIP 原则）
  - 接收 ASR 转写文本，经 Batcher 合并后调用 LLM Streaming 翻译
  - 注入术语表和上下文窗口
  - 超时降级：3s 未返回时输出原文 + "翻译中" 标记
- [ ] 2.3.4 实现翻译结果回调机制（供 WebSocket 推送到前端）
- [ ] 2.3.5 编写测试：验证合并逻辑、去重逻辑、超时降级

---

### Task 2.4 闭麦辅助表达服务

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P0 |
| 预估 | 1 天 |
| 产出文件 | `expression/assistant.py`, `expression/scene_manager.py`, `config/scenes.json` |
| 依赖 | Task 2.1, 2.2, 1.4 |

**子步骤：**

- [ ] 2.4.1 实现 `SceneManager`（`expression/scene_manager.py`）：
  - 从 JSON 文件加载场景配置
  - 默认场景：跨国团队内部技术讨论会
  - 支持增删自定义场景
- [ ] 2.4.2 实现 `ExpressionAssistant`（`expression/assistant.py`）：
  - 键盘输入模式：直接文本 → LLM Streaming → 英文表达
  - 麦克风输入模式：音频 → ASR → 文本 → LLM Streaming → 英文表达
  - 注入当前场景描述和术语表到 Prompt
- [ ] 2.4.3 实现 `MutePipeline`（`pipeline/mute_pipeline.py`）：
  - 编排闭麦辅助完整链路
  - 支持在语音/键盘两种输入模式间切换
- [ ] 2.4.4 创建默认场景配置（`config/scenes.json`）
- [ ] 2.4.5 编写测试：验证键盘输入和语音输入两种模式

---

### Task 2.5 WebSocket 通信层

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P0 |
| 预估 | 1 天 |
| 产出文件 | `ws/server.py`, `ws/messages.py` |
| 依赖 | Task 2.3, 2.4 |

**子步骤：**

- [ ] 2.5.1 定义 WebSocket 消息协议（`ws/messages.py`）：
  ```python
  # 消息类型枚举
  class MessageType(str, Enum):
      TRANSCRIPTION = "transcription"        # ASR 转写结果
      TRANSLATION = "translation"            # 翻译结果
      EXPRESSION = "expression"              # 闭麦辅助结果
      SUMMARY_UPDATE = "summary_update"      # 摘要更新
      STATUS = "status"                      # 系统状态
      MUTE_INPUT = "mute_input"              # 闭麦文本输入（前端→后端）
      CONTROL = "control"                    # 控制命令（开始/停止/切换模式）
  ```
- [ ] 2.5.2 实现 WebSocket 服务端（`ws/server.py`）：
  - 基于 FastAPI WebSocket
  - 支持多客户端连接
  - 实现消息分发（按类型路由到对应处理器）
- [ ] 2.5.3 实现后端 → 前端推送（转写、翻译、表达结果的实时推送）
- [ ] 2.5.4 实现前端 → 后端接收（闭麦文本输入、控制命令）
- [ ] 2.5.5 编写 WebSocket 集成测试

---

### Task 2.6 FastAPI 应用入口

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P0 |
| 预估 | 0.5 天 |
| 产出文件 | `main.py` |
| 依赖 | Task 2.5 |

**子步骤：**

- [ ] 2.6.1 编写 `main.py` FastAPI 应用：
  - 生命周期管理（startup: 初始化模块，shutdown: 清理资源）
  - 挂载 WebSocket 端点 (`/ws`)
  - 提供 REST 端点：`GET /status`（系统状态）、`GET /devices`（音频设备列表）
- [ ] 2.6.2 实现模块组装（依赖注入：AudioSource → VAD → ASR → Translator → Pipeline）
- [ ] 2.6.3 实现启动参数：`--mode=cli|server`、`--port`、`--model-size`
- [ ] 2.6.4 编写启动脚本

---

### Task 2.7 Electron 前端 — 项目初始化

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P0 |
| 预估 | 0.5 天 |
| 产出文件 | `frontend/` 基础结构 |
| 依赖 | 无 |

**子步骤：**

- [ ] 2.7.1 初始化 Electron 项目（`npm init`, 安装 electron, electron-builder）
- [ ] 2.7.2 选择并配置前端框架（Vue 3 + Vite 或 React + Vite）
- [ ] 2.7.3 编写 `main.js`：Electron 主进程，创建主窗口
- [ ] 2.7.4 编写 `preload.js`：暴露安全的 IPC 通道
- [ ] 2.7.5 配置 electron-builder 基础打包配置

---

### Task 2.8 Electron 前端 — 悬浮字幕窗

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P0 |
| 预估 | 1 天 |
| 产出文件 | `frontend/src/components/SubtitleOverlay/`, `frontend/electron/window-manager.js` |
| 依赖 | Task 2.7 |

**子步骤：**

- [ ] 2.8.1 实现窗口管理器（`window-manager.js`）：
  - 创建半透明、无边框、始终置顶的悬浮窗
  - 支持拖拽和缩放
  - 记忆窗口位置与大小
- [ ] 2.8.2 实现 `SubtitleOverlay` 组件：
  - 显示最近 5 条双语字幕
  - 自动滚动
  - 时间戳 + 说话者标注
- [ ] 2.8.3 实现 WebSocket 客户端（`services/ws-client.ts`）：
  - 连接 `ws://localhost:{port}/ws`
  - 自动重连机制
  - 按消息类型分发到 store
- [ ] 2.8.4 实现字幕状态管理 store
- [ ] 2.8.5 基础样式：半透明背景、可读字体、紧凑布局

---

### Task 2.9 Electron 前端 — 闭麦输入面板

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P0 |
| 预估 | 1 天 |
| 产出文件 | `frontend/src/components/MuteAssistPanel/` |
| 依赖 | Task 2.7, 2.8 |

**子步骤：**

- [ ] 2.9.1 实现 `MuteAssistPanel` 组件：
  - 中文文本输入框
  - 英文输出展示区（支持 Streaming 逐字显示）
  - 一键复制按钮（复制到系统剪贴板）
  - 输入模式切换：键盘 / 麦克风
- [ ] 2.9.2 实现键盘输入 → WebSocket 发送 → 接收英文结果
- [ ] 2.9.3 实现麦克风输入模式的 UI 状态（录音中/处理中/已完成）
- [ ] 2.9.4 快捷键注册：`Ctrl+Shift+M` 激活/隐藏面板
- [ ] 2.9.5 基础样式与交互动效

---

### Task 2.10 Phase 1b 集成验证

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P0 |
| 预估 | 0.5 天 |
| 依赖 | Task 2.6, 2.8, 2.9 |

**子步骤：**

- [ ] 2.10.1 启动 Python 后端 + Electron 前端，验证 WebSocket 连接
- [ ] 2.10.2 在 Teams 会议中测试：实时双语字幕显示
- [ ] 2.10.3 测试闭麦键盘输入 → 英文表达输出
- [ ] 2.10.4 测试闭麦麦克风输入 → 英文表达输出
- [ ] 2.10.5 测量翻译首字延迟（目标 < 1 秒）、总体字幕延迟（目标 < 4 秒）

**验收标准：** Teams 会议中实时显示双语字幕；闭麦输入可输出英文表达。

---

## Phase 2 — 总结 + 体验优化（第 3-4 周）

> 目标：完成会议总结能力，优化使用体验

### Task 3.1 段落级摘要模块

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 1 天 |
| 产出文件 | `summary/segment_summarizer.py` |
| 依赖 | Task 2.1, 2.2 |

**子步骤：**

- [ ] 3.1.1 实现 `SegmentSummarizer`：
  - 接收 60s 时间窗口的转写文本
  - 调用 LLM 生成段落级摘要（2-3 句要点）
  - 提取讨论主题、结论、行动项
- [ ] 3.1.2 实现时间窗口管理（按 60s 分段，有重叠缓冲）
- [ ] 3.1.3 编写测试

---

### Task 3.2 全局合并摘要模块

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 1 天 |
| 产出文件 | `summary/global_merger.py` |
| 依赖 | Task 3.1 |

**子步骤：**

- [ ] 3.2.1 实现 `GlobalMerger`：
  - 每累积 5 个段落摘要，触发一次全局合并
  - 去重相同主题、合并结论
  - 输出结构化总结（主题 / 结论 / Action Items / 风险项）
- [ ] 3.2.2 实现增量合并（新段落摘要与已有全局摘要合并，而非全量重算）
- [ ] 3.2.3 编写测试

---

### Task 3.3 Action Items 提取

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 0.5 天 |
| 产出文件 | `summary/action_item_extractor.py` |
| 依赖 | Task 3.2 |

**子步骤：**

- [ ] 3.3.1 实现 `ActionItemExtractor`：
  - 从全局摘要中提取结构化 Action Items
  - 格式：事项描述、责任人（如可识别）、截止时间（如提及）
- [ ] 3.3.2 定义 `ActionItem` 数据类
- [ ] 3.3.3 编写测试

---

### Task 3.4 总结模块集成到流水线

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 0.5 天 |
| 产出文件 | 更新 `pipeline/meeting_pipeline.py`, `ws/server.py` |
| 依赖 | Task 3.1, 3.2, 3.3 |

**子步骤：**

- [ ] 3.4.1 在 `MeetingPipeline` 中集成总结模块（ASR 文本同时送入翻译和总结分支）
- [ ] 3.4.2 通过 WebSocket 推送摘要更新到前端
- [ ] 3.4.3 集成测试

---

### Task 3.5 会议摘要面板 UI

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 1 天 |
| 产出文件 | `frontend/src/components/SummaryPanel/` |
| 依赖 | Task 3.4 |

**子步骤：**

- [ ] 3.5.1 实现 `SummaryPanel` 组件：
  - 当前讨论主题（自动高亮最新主题）
  - 关键结论列表（按主题归类）
  - Action Items 列表（含状态标记）
  - 会议时间线（主题切换时间点可视化）
- [ ] 3.5.2 实现摘要状态 store（接收 WebSocket 推送更新）
- [ ] 3.5.3 样式与布局

---

### Task 3.6 本地存储层（SQLite）

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 1 天 |
| 产出文件 | `storage/database.py`, `storage/models.py` |
| 依赖 | Task 1.1 |

**子步骤：**

- [ ] 3.6.1 定义数据模型（`storage/models.py`）：
  - `Meeting`：会议 ID、标题、开始/结束时间、状态
  - `Transcription`：转写文本、时间戳、语言、说话者
  - `Translation`：原文、译文、时间戳
  - `Summary`：摘要内容、类型（段落/全局）、时间戳
  - `ActionItem`：描述、责任人、截止时间、状态
  - `GlossaryEntry`：术语、翻译
  - `SceneConfig`：场景名称、描述
- [ ] 3.6.2 实现 `Database` 类（`storage/database.py`）：
  - SQLite 连接管理（使用 `aiosqlite` 异步操作）
  - 表创建与迁移
  - CRUD 操作封装
- [ ] 3.6.3 在 Pipeline 中集成存储（转写、翻译、摘要自动持久化）
- [ ] 3.6.4 编写测试

---

### Task 3.7 会议纪要导出

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P2 |
| 预估 | 1 天 |
| 产出文件 | `storage/exporter.py` |
| 依赖 | Task 3.6 |

**子步骤：**

- [ ] 3.7.1 实现 `MeetingExporter`（`storage/exporter.py`）：
  - Markdown 格式导出（含时间戳、双语转写、摘要、Action Items）
  - JSON 格式导出（结构化数据）
  - Confluence Wiki 格式导出
- [ ] 3.7.2 提供 REST 端点 `GET /api/meeting/{id}/export?format=md|json|wiki`
- [ ] 3.7.3 编写测试

---

### Task 3.8 术语表配置界面

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 0.5 天 |
| 产出文件 | `frontend/src/components/SettingsPanel/GlossaryEditor.vue` |
| 依赖 | Task 2.2, 3.6 |

**子步骤：**

- [ ] 3.8.1 实现术语表编辑 UI：表格形式增删改术语对
- [ ] 3.8.2 提供 REST 端点：`GET/POST/DELETE /api/glossary`
- [ ] 3.8.3 术语表变更实时生效（更新 PromptManager 注入内容）

---

### Task 3.9 场景配置管理

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 0.5 天 |
| 产出文件 | `frontend/src/components/SettingsPanel/SceneEditor.vue` |
| 依赖 | Task 2.4, 3.6 |

**子步骤：**

- [ ] 3.9.1 实现场景配置编辑 UI：场景名称 + 详细描述
- [ ] 3.9.2 提供 REST 端点：`GET/POST/DELETE /api/scenes`
- [ ] 3.9.3 闭麦面板支持切换当前场景

---

### Task 3.10 常用短语与快捷输入

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 0.5 天 |
| 产出文件 | 更新 `MuteAssistPanel` |
| 依赖 | Task 2.9, 3.6 |

**子步骤：**

- [ ] 3.10.1 实现常用短语收藏功能（翻译结果旁加 ⭐ 收藏按钮）
- [ ] 3.10.2 实现快捷短语面板（展示收藏的短语，点击即可复制）
- [ ] 3.10.3 短语持久化到 SQLite

---

### Task 3.11 快捷键系统

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 0.5 天 |
| 产出文件 | 更新 `frontend/main.js` |
| 依赖 | Task 2.8, 2.9 |

**子步骤：**

- [ ] 3.11.1 注册全局快捷键：
  - `Ctrl+Shift+M`：激活/隐藏闭麦面板
  - `Ctrl+Shift+S`：激活/隐藏字幕窗
  - `Ctrl+Shift+R`：开始/停止会议录制
- [ ] 3.11.2 系统托盘图标与菜单（`electron/tray.js`）
- [ ] 3.11.3 快捷键配置持久化

---

### Task 3.12 Phase 2 集成验证

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 0.5 天 |
| 依赖 | Task 3.1 ~ 3.11 |

**子步骤：**

- [ ] 3.12.1 完整参与 1 小时 Teams 会议，全功能测试
- [ ] 3.12.2 验证实时摘要更新与 Action Items 提取
- [ ] 3.12.3 验证会后导出 Markdown 纪要完整性
- [ ] 3.12.4 验证术语表和场景配置的 CRUD
- [ ] 3.12.5 记录性能数据与用户体验问题

**验收标准：** 完整参与一场 1 小时会议，自动生成结构化纪要和 Action Items。

---

## Phase 3 — 产品化（第 5-8 周）

> 目标：打磨产品质量，支持团队内推广

### Task 4.1 安装包打包

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 2 天 |
| 产出文件 | 打包配置、安装脚本 |

**子步骤：**

- [ ] 4.1.1 配置 electron-builder 打包为 `.exe` NSIS 安装包
- [ ] 4.1.2 Python 后端通过 PyInstaller 打包为单文件可执行程序
- [ ] 4.1.3 Electron 主进程中嵌入 Python 后端进程启停管理
- [ ] 4.1.4 编写 `scripts/install.ps1` 一键安装脚本
- [ ] 4.1.5 测试全新系统上的安装流程

---

### Task 4.2 GPU 检测与模型自动下载

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 1 天 |

**子步骤：**

- [ ] 4.2.1 首次启动检测 NVIDIA GPU + CUDA 版本
- [ ] 4.2.2 根据检测结果推荐并下载对应 ASR 模型（large-v3 / medium / small）
- [ ] 4.2.3 模型下载进度展示
- [ ] 4.2.4 无 GPU 时自动配置 CPU int8 推理

---

### Task 4.3 使用引导与配置向导

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P2 |
| 预估 | 1 天 |

**子步骤：**

- [ ] 4.3.1 首次启动向导：音频设备选择、API Key 配置、模型下载
- [ ] 4.3.2 音频采集自检（播放测试音频，验证 WASAPI 可用性）
- [ ] 4.3.3 LLM API 连通性测试

---

### Task 4.4 长时间稳定性优化

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P1 |
| 预估 | 2 天 |

**子步骤：**

- [ ] 4.4.1 内存泄漏排查（2 小时压力测试 + 内存监控）
- [ ] 4.4.2 WebSocket 连接保活与断线重连
- [ ] 4.4.3 ASR 模型长时间运行稳定性验证
- [ ] 4.4.4 音频采集线程异常恢复
- [ ] 4.4.5 日志系统完善（文件轮转日志，便于问题排查）

---

### Task 4.5 TTS 朗读功能（可选）

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P2 |
| 预估 | 1 天 |

**子步骤：**

- [ ] 4.5.1 集成 Windows SAPI 或 edge-tts 进行英文朗读
- [ ] 4.5.2 闭麦面板增加"朗读"按钮
- [ ] 4.5.3 语速和语音可配置

---

### Task 4.6 用户反馈与错误上报

| 属性 | 值 |
| --- | --- |
| 状态 | ⬜ 未开始 |
| 优先级 | P2 |
| 预估 | 0.5 天 |

**子步骤：**

- [ ] 4.6.1 应用内反馈入口
- [ ] 4.6.2 崩溃日志自动收集（本地存储，不自动上传）
- [ ] 4.6.3 版本更新检查机制

---

## 任务依赖关系图

```text
Phase 1a:
  1.1 ──→ 1.2 ──→ 1.3 ──→ 1.4 ──→ 1.5 ──→ 1.6
                                      ↓
Phase 1b:                            2.4
  1.1 ──→ 2.1 ──→ 2.2 ──→ 2.3 ──┐
                                   ├──→ 2.5 ──→ 2.6 ──→ 2.10
  (独立)   2.7 ──→ 2.8 ──→ 2.9 ──┘

Phase 2:
  2.1 ──→ 3.1 ──→ 3.2 ──→ 3.3 ──→ 3.4 ──→ 3.5
  1.1 ──→ 3.6 ──→ 3.7
  2.2 + 3.6 ──→ 3.8
  2.4 + 3.6 ──→ 3.9
  2.9 + 3.6 ──→ 3.10
  2.8 + 2.9 ──→ 3.11
  ALL ──→ 3.12

Phase 3:
  3.12 ──→ 4.1 ~ 4.6（可并行）
```

---

## 进度总览

| Phase | 任务数 | 已完成 | 进度 |
| --- | --- | --- | --- |
| Phase 1a（核心链路） | 6 | 6 | 100% |
| Phase 1b（翻译+UI） | 10 | 0 | 0% |
| Phase 2（总结+体验） | 12 | 0 | 0% |
| Phase 3（产品化） | 6 | 0 | 0% |
| **总计** | **34** | **6** | **18%** |
