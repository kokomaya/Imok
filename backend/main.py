"""应用入口 — 支持 CLI 模式和子进程模式。

CLI 模式 (--mode=cli)：实时打印 ASR 转写结果到终端，用于核心链路验证。
Subprocess 模式 (--mode=subprocess)：作为 Electron 子进程运行，
    通过 stdin/stdout JSON Lines 与主进程通信。

使用方式：
    python -m backend.main --mode=cli --source=wasapi
    python -m backend.main --mode=cli --source=mic
    python -m backend.main --mode=subprocess --source=wasapi
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys


def _setup_logging(level: str = "INFO", *, stderr_only: bool = False) -> None:
    """配置日志。subprocess 模式下日志输出到 stderr，避免污染 stdout JSON Lines。"""
    handler = logging.StreamHandler(sys.stderr if stderr_only else None)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IMOK AI Meeting Assistant",
    )
    parser.add_argument(
        "--mode",
        choices=["cli", "subprocess"],
        default="cli",
        help="运行模式: cli (终端输出) 或 subprocess (Electron 子进程 IPC)",
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


def _create_audio_source(source_type: str):
    """创建音频源（CLI 和 subprocess 共享逻辑）。"""
    from backend.config import get_settings

    settings = get_settings()

    if source_type == "wasapi":
        from backend.audio.wasapi_source import WASAPILoopbackSource

        return WASAPILoopbackSource(
            target_sample_rate=settings.audio.sample_rate,
            chunk_frames=settings.audio.chunk_frames,
        )
    else:
        from backend.audio.mic_source import MicrophoneSource

        return MicrophoneSource(
            target_sample_rate=settings.audio.sample_rate,
            chunk_frames=settings.audio.chunk_frames,
            device_index=settings.audio.mic_device,
        )


def _create_vad():
    """创建 VAD 实例。"""
    from backend.config import get_settings
    from backend.asr.vad import VoiceActivityDetector

    settings = get_settings()
    return VoiceActivityDetector(
        sample_rate=settings.audio.sample_rate,
        threshold=settings.asr.vad_threshold,
        min_silence_ms=settings.asr.vad_min_silence_ms,
        max_segment_s=settings.asr.vad_max_segment_s,
    )


def _create_asr():
    """创建 ASR 引擎。"""
    from backend.config import get_settings
    from backend.asr.whisper_engine import WhisperEngine

    settings = get_settings()
    return WhisperEngine(settings.asr)


async def _run_subprocess(source_type: str) -> None:
    """Subprocess 模式 — 作为 Electron 子进程运行。

    通过 stdout 输出 JSON Lines 消息（转写结果、状态），
    通过 stdin 接收控制命令（start/stop/switch_source）。
    所有日志输出到 stderr。

    生命周期：
    1. 启动 → stdout 输出 {"type": "status", "data": {"state": "ready"}}
    2. 收到 stdin control:start → 加载模型 → 启动 Pipeline → stdout RUNNING
    3. Pipeline 运行中 → stdout 输出 transcription 消息
    4. 收到 stdin control:stop → 停止 Pipeline → stdout STOPPED
    5. stdin EOF 或 SIGINT → 优雅退出
    """
    from backend.ipc.messages import (
        ControlAction,
        IPCMessage,
        MessageType,
        ProcessState,
    )
    from backend.ipc.subprocess_io import SubprocessReader, SubprocessWriter
    from backend.pipeline.meeting_pipeline import MeetingPipeline, TranscriptionEvent

    logger = logging.getLogger(__name__)
    writer = SubprocessWriter()

    # 通知 Electron：子进程已就绪
    writer.write(IPCMessage.status(ProcessState.READY))

    pipeline = None
    stop_event = asyncio.Event()
    current_source_type = source_type
    # 持有事件循环引用，供从 reader 线程安全调度协程
    main_loop = asyncio.get_running_loop()

    def _on_transcription(event: TranscriptionEvent) -> None:
        """Pipeline 转写回调 → 写 JSON Lines 到 stdout。"""
        r = event.result
        msg = IPCMessage.transcription(
            r.text,
            language=r.language,
            confidence=r.language_probability,
            segment_start=event.segment_start_time,
            segment_end=event.segment_end_time,
        )
        writer.write(msg)

    async def _start_pipeline(src_type: str) -> MeetingPipeline:
        """创建并启动 Pipeline。"""
        writer.write(IPCMessage.status(ProcessState.LOADING, message="Loading models..."))

        try:
            audio_src = _create_audio_source(src_type)
            vad = _create_vad()
            asr = _create_asr()
            asr.load()

            pl = MeetingPipeline(audio_src, vad, asr)
            pl.on_transcription(_on_transcription)
            await pl.start()

            writer.write(
                IPCMessage.status(
                    ProcessState.RUNNING,
                    source=src_type,
                    asr_model=asr._settings.model_size if hasattr(asr, "_settings") else "",
                )
            )
            return pl
        except Exception as exc:
            logger.exception("Failed to start pipeline")
            writer.write(IPCMessage.error("pipeline_start_failed", str(exc)))
            raise

    async def _stop_pipeline() -> None:
        nonlocal pipeline
        if pipeline is not None:
            await pipeline.stop()
            pipeline = None
            writer.write(IPCMessage.status(ProcessState.STOPPED))

    def _handle_control(message: IPCMessage) -> None:
        """处理 stdin 控制命令（从 reader 线程调用，通过 main_loop 调度协程）。"""
        nonlocal current_source_type
        action = message.data.get("action", "")

        if action == ControlAction.START:
            asyncio.run_coroutine_threadsafe(_do_start(), main_loop)
        elif action == ControlAction.STOP:
            asyncio.run_coroutine_threadsafe(_do_stop(), main_loop)
        elif action == ControlAction.SWITCH_SOURCE:
            new_source = message.data.get("source", "")
            if new_source in ("wasapi", "mic"):
                current_source_type = new_source
                asyncio.run_coroutine_threadsafe(_do_restart(new_source), main_loop)
            else:
                writer.write(
                    IPCMessage.error("invalid_source", f"Unknown source: {new_source}")
                )
        else:
            logger.warning("Unknown control action: %s", action)

    async def _do_start() -> None:
        nonlocal pipeline
        if pipeline is not None:
            return
        try:
            pipeline = await _start_pipeline(current_source_type)
        except Exception:
            pass  # 错误已在 _start_pipeline 中通过 IPC 报告

    async def _do_stop() -> None:
        await _stop_pipeline()

    async def _do_restart(new_source: str) -> None:
        nonlocal pipeline
        await _stop_pipeline()
        try:
            pipeline = await _start_pipeline(new_source)
        except Exception:
            pass

    # 设置 stdin 读取器
    reader = SubprocessReader()
    reader.on_message(MessageType.CONTROL, _handle_control)
    await reader.start()

    # stdin EOF 时自动触发停止（Electron 关闭管道 = 子进程应退出）
    async def _watch_reader() -> None:
        """等待 reader 结束（stdin EOF），然后触发优雅退出。"""
        while reader._running:
            await asyncio.sleep(0.1)
        logger.debug("Stdin reader ended, triggering shutdown.")
        stop_event.set()

    watcher_task = asyncio.create_task(_watch_reader())

    # Windows 信号处理
    if sys.platform == "win32":
        signal.signal(signal.SIGINT, lambda s, f: stop_event.set())
    else:
        main_loop.add_signal_handler(signal.SIGINT, stop_event.set)

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await _stop_pipeline()
        await reader.stop()
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass
        writer.close()


def main() -> None:
    args = _parse_args()

    if args.mode == "subprocess":
        _setup_logging(args.log_level, stderr_only=True)
        asyncio.run(_run_subprocess(args.source))
    elif args.mode == "cli":
        _setup_logging(args.log_level)
        asyncio.run(_run_cli(args.source))
    else:
        print(f"Unknown mode: {args.mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
