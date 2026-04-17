"""全局配置模块 — 使用 pydantic-settings 管理分层配置。

职责划分（SRP）：
- AudioSettings:  音频采集参数
- ASRSettings:    语音识别参数（模型、VAD）
- LLMSettings:    大模型 API 连接参数
- ServerSettings: FastAPI / WebSocket 服务参数
- PathSettings:   文件路径配置
- AppSettings:    顶层聚合，一次性加载所有子配置

配置优先级：环境变量 > .env 文件 > 默认值
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# 项目根目录（backend/ 的父级）
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_ROOT = Path(__file__).resolve().parent


# ===========================================================================
# 音频采集配置
# ===========================================================================
class AudioSettings(BaseSettings):
    """音频采集相关参数。"""

    model_config = SettingsConfigDict(env_prefix="IMOK_AUDIO_")

    sample_rate: int = Field(default=16000, description="目标采样率 (Hz)")
    channels: int = Field(default=1, description="声道数")
    dtype: str = Field(default="int16", description="采样精度")
    chunk_frames: int = Field(
        default=512,
        description="每次读取的帧数 (512~1024)",
    )
    loopback_device: int | None = Field(
        default=None,
        description="WASAPI loopback 设备 ID，None 为自动检测",
    )
    mic_device: int | None = Field(
        default=None,
        description="麦克风设备 ID，None 为系统默认",
    )


# ===========================================================================
# 语音识别配置
# ===========================================================================
class ASRSettings(BaseSettings):
    """ASR 引擎与 VAD 相关参数。"""

    model_config = SettingsConfigDict(env_prefix="IMOK_ASR_")

    model_size: str | None = Field(
        default=None,
        description="Whisper 模型尺寸，None 表示根据 GPU 自动选择",
    )
    compute_type: str | None = Field(
        default=None,
        description="计算精度，None 表示根据 GPU 自动选择",
    )
    device: str | None = Field(
        default=None,
        description="推理设备 ('cuda'/'cpu')，None 表示自动检测",
    )
    language: str | None = Field(
        default=None,
        description="识别语言，None 为自动检测（支持中英混合）",
    )
    beam_size: int = Field(default=3, description="Beam search 宽度")
    vad_threshold: float = Field(default=0.5, description="VAD 置信度阈值")
    vad_min_silence_ms: int = Field(default=300, description="最小静音间隔 (ms)")
    vad_max_segment_s: float = Field(default=15.0, description="最大语音段时长 (s)")

    def resolve_with_gpu(self) -> ASRSettings:
        """如果 model_size / compute_type / device 未显式设置，则通过 GPU 检测自动填充。"""
        if self.model_size and self.compute_type and self.device:
            return self

        # 延迟导入，避免在无 torch 环境下启动失败
        sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
        from detect_gpu import detect_gpu

        gpu = detect_gpu()
        return self.model_copy(
            update={
                "model_size": self.model_size or gpu.recommended_model_size,
                "compute_type": self.compute_type or gpu.recommended_compute_type,
                "device": self.device or gpu.recommended_device,
            }
        )


# ===========================================================================
# LLM 配置
# ===========================================================================
class LLMSettings(BaseSettings):
    """公司 LLM API 连接参数。"""

    model_config = SettingsConfigDict(env_prefix="IMOK_LLM_")

    api_base_url: str = Field(
        default="http://localhost:8080/v1",
        description="LLM API base URL",
    )
    api_key: str = Field(
        default="",
        description="API Key（建议通过环境变量 IMOK_LLM_API_KEY 设置）",
    )
    model_name: str = Field(default="default", description="模型名称")
    timeout_s: float = Field(default=30.0, description="单次请求超时 (s)")
    max_retries: int = Field(default=3, description="最大重试次数")
    translation_timeout_s: float = Field(
        default=3.0,
        description="翻译降级超时阈值 (s)",
    )
    request_merge_ms: int = Field(
        default=500,
        description="翻译请求合并窗口 (ms)",
    )
    max_requests_per_minute: int = Field(
        default=60,
        description="每分钟最大请求数",
    )


# ===========================================================================
# 服务配置
# ===========================================================================
class ServerSettings(BaseSettings):
    """FastAPI / WebSocket 服务参数。"""

    model_config = SettingsConfigDict(env_prefix="IMOK_SERVER_")

    host: str = Field(default="127.0.0.1", description="监听地址")
    port: int = Field(default=18900, description="监听端口")
    log_level: str = Field(default="info", description="日志级别")


# ===========================================================================
# 路径配置
# ===========================================================================
class PathSettings(BaseSettings):
    """文件路径配置。"""

    model_config = SettingsConfigDict(env_prefix="IMOK_PATH_")

    project_root: Path = Field(default=_PROJECT_ROOT)
    models_dir: Path = Field(
        default=_PROJECT_ROOT / "models",
        description="ASR 模型存放目录",
    )
    config_dir: Path = Field(
        default=_PROJECT_ROOT / "config",
        description="配置文件目录",
    )
    data_dir: Path = Field(
        default=_PROJECT_ROOT / "data",
        description="运行时数据目录（SQLite 等）",
    )
    glossary_file: Path = Field(
        default=_PROJECT_ROOT / "config" / "glossary.json",
        description="默认术语表路径",
    )
    scenes_file: Path = Field(
        default=_PROJECT_ROOT / "config" / "scenes.json",
        description="默认场景配置路径",
    )
    providers_file: Path = Field(
        default=_PROJECT_ROOT / "config" / "llm_providers.yaml",
        description="LLM 提供商配置路径",
    )

    def ensure_dirs(self) -> None:
        """确保所有必要目录存在。"""
        for d in (self.models_dir, self.config_dir, self.data_dir):
            d.mkdir(parents=True, exist_ok=True)


# ===========================================================================
# 顶层聚合配置
# ===========================================================================
class AppSettings(BaseSettings):
    """应用全局配置 — 聚合所有子配置。"""

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    audio: AudioSettings = Field(default_factory=AudioSettings)
    asr: ASRSettings = Field(default_factory=ASRSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    paths: PathSettings = Field(default_factory=PathSettings)


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """获取全局配置单例（首次调用时从环境 / .env 加载）。"""
    settings = AppSettings()
    settings.paths.ensure_dirs()
    settings.asr = settings.asr.resolve_with_gpu()
    return settings
