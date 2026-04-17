# Imok — AI 实时会议翻译与总结助手

外挂式 AI 会议助手，为 Microsoft Teams 会议提供实时双语字幕、闭麦辅助表达和自动会议总结。

## 核心功能

- **实时双语字幕** — 采集 Teams 会议音频，ASR 转写 + LLM 翻译，中英双语悬浮字幕
- **闭麦辅助表达** — Teams 闭麦时，通过麦克风或键盘输入中文，实时生成英文会议表达
- **自动会议总结** — 增量式会议摘要，自动提取 Action Items
- **会议纪要导出** — Markdown / JSON / Confluence 格式

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 后端 | Python, FastAPI, WebSocket |
| ASR | faster-whisper + Silero-VAD |
| LLM | 公司内部大模型 API (Streaming SSE) |
| 前端 | Electron + Vue 3 / React |
| 存储 | SQLite |

## 快速开始

### 1. 环境准备

- Python 3.11+
- Node.js 18+ (前端)
- NVIDIA GPU + CUDA 12.x (推荐，非必须)

### 2. 后端安装

```bash
cd Imok

# 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate    # Windows

# 安装依赖
pip install -r requirements.txt

# (可选) 安装开发依赖
pip install -r requirements-dev.txt
```

### 3. GPU 检测

```bash
python scripts/detect_gpu.py
```

### 4. 配置

复制 `.env.example` 为 `.env`，填入 LLM API 配置：

```env
IMOK_LLM_API_BASE_URL=http://your-llm-api/v1
IMOK_LLM_API_KEY=your-api-key
IMOK_LLM_MODEL_NAME=your-model
```

### 5. 启动后端

```bash
python -m backend.main --mode=server
```

## 项目结构

```
Imok/
├── backend/              # Python 后端
│   ├── config.py         # 全局配置
│   ├── audio/            # 音频采集
│   ├── asr/              # 语音识别 (ASR + VAD)
│   ├── llm/              # LLM 客户端 / Prompt / 术语表
│   ├── translation/      # 实时翻译
│   ├── expression/       # 闭麦辅助表达
│   ├── summary/          # 会议总结
│   ├── storage/          # SQLite 持久化
│   ├── pipeline/         # 流水线编排
│   └── ws/               # WebSocket 通信
├── frontend/             # Electron 前端
├── config/               # 默认配置文件
├── scripts/              # 工具脚本
├── tests/                # 测试
└── prompt/               # 项目文档
```

## 文档

- [方案计划书](prompt/plan_optimized.md)
- [任务拆解与追踪](prompt/tasks.md)
