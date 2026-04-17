"""应用入口 — 支持 CLI 模式和 Server 模式。

CLI 模式 (--mode=cli)：实时打印 ASR 转写结果到终端，用于核心链路验证。
Server 模式 (--mode=server)：启动 FastAPI + WebSocket 服务（Phase 1b 实现）。

使用方式：
    python -m backend.main --mode=cli --source=wasapi
    python -m backend.main --mode=cli --source=mic
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IMOK AI Meeting Assistant",
    )
    parser.add_argument(
        "--mode",
        choices=["cli", "server"],
        default="cli",
        help="运行模式: cli (终端输出) 或 server (FastAPI)",
    )
    parser.add_argument(
        "--source",
        choices=["wasapi", "mic"],
        default="wasapi",
        help="音频源: wasapi (系统音频) 或 mic (麦克风)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="日志级别",
    )
    return parser.parse_args()


def _print_transcription(event) -> None:
    """CLI 模式的转写回调 — 格式化打印到终端。"""
    from backend.pipeline.meeting_pipeline import TranscriptionEvent

    e: TranscriptionEvent = event
    r = e.result
    time_range = f"[{e.segment_start_time:6.1f}s - {e.segment_end_time:6.1f}s]"
    lang_tag = f"({r.language})" if r.language else ""
    print(f"  {time_range} {lang_tag} {r.text}")


async def _run_cli(source_type: str) -> None:
    """CLI 模式 — 实时采集音频并打印 ASR 转写结果。"""
    from backend.config import get_settings
    from backend.asr.base import TranscriptionResult
    from backend.asr.vad import VoiceActivityDetector
    from backend.asr.whisper_engine import WhisperEngine
    from backend.pipeline.meeting_pipeline import MeetingPipeline

    settings = get_settings()

    # 创建音频源
    if source_type == "wasapi":
        from backend.audio.wasapi_source import WASAPILoopbackSource

        audio_source = WASAPILoopbackSource(
            target_sample_rate=settings.audio.sample_rate,
            chunk_frames=settings.audio.chunk_frames,
        )
        print(f"[Audio] Using WASAPI Loopback (system audio)")
    else:
        from backend.audio.mic_source import MicrophoneSource

        audio_source = MicrophoneSource(
            target_sample_rate=settings.audio.sample_rate,
            chunk_frames=settings.audio.chunk_frames,
            device_index=settings.audio.mic_device,
        )
        print(f"[Audio] Using Microphone")

    # 创建 VAD
    print(f"[VAD] Loading Silero-VAD...")
    vad = VoiceActivityDetector(
        sample_rate=settings.audio.sample_rate,
        threshold=settings.asr.vad_threshold,
        min_silence_ms=settings.asr.vad_min_silence_ms,
        max_segment_s=settings.asr.vad_max_segment_s,
    )

    # 创建 ASR 引擎
    print(
        f"[ASR] Whisper: model={settings.asr.model_size}, "
        f"device={settings.asr.device}, compute={settings.asr.compute_type}"
    )
    print(f"[ASR] Model will be loaded on first transcription (lazy loading)...")
    asr = WhisperEngine(settings.asr)

    # 预加载 ASR 模型
    print(f"[ASR] Pre-loading model (first time may download from HuggingFace)...")
    asr.load()
    print(f"[ASR] Model ready.")

    # 组装流水线
    pipeline = MeetingPipeline(audio_source, vad, asr)
    pipeline.on_transcription(_print_transcription)

    # 启动
    print()
    print("=" * 60)
    print("  IMOK Meeting Assistant — CLI Mode")
    print("  Press Ctrl+C to stop")
    print("=" * 60)
    print()

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        print("\n[Stop] Shutting down...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    # Windows 不支持 loop.add_signal_handler，使用 signal 模块
    if sys.platform == "win32":
        signal.signal(signal.SIGINT, lambda s, f: _signal_handler())
    else:
        loop.add_signal_handler(signal.SIGINT, _signal_handler)

    try:
        await pipeline.start()
        print("[Running] Listening for audio... (transcriptions will appear below)")
        print()

        # 等待停止信号
        await stop_event.wait()

    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"\n[Error] {exc}")
        logging.getLogger(__name__).exception("Pipeline error")
    finally:
        await pipeline.stop()
        print()
        print(
            f"[Done] Processed {pipeline.segments_processed} segments, "
            f"{pipeline._total_audio_duration:.1f}s total audio."
        )


def main() -> None:
    args = _parse_args()
    _setup_logging(args.log_level)

    if args.mode == "cli":
        asyncio.run(_run_cli(args.source))
    elif args.mode == "server":
        print("[Server mode not yet implemented. Use --mode=cli for now.]")
        sys.exit(1)


if __name__ == "__main__":
    main()
