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
    """创建音频源。

    支持的 source_type:
    - 'wasapi': 仅系统音频
    - 'mic': 仅麦克风
    - 'both': 系统音频 + 麦克风混合
    """
    from backend.config import get_settings

    settings = get_settings()

    if source_type == "both":
        from backend.audio.mixer import AudioMixer
        from backend.audio.mic_source import MicrophoneSource
        from backend.audio.wasapi_source import WASAPILoopbackSource

        mixer = AudioMixer(
            target_sample_rate=settings.audio.sample_rate,
            chunk_duration_s=settings.audio.chunk_frames / settings.audio.sample_rate,
        )
        mixer.add_source(
            "wasapi",
            WASAPILoopbackSource(
                target_sample_rate=settings.audio.sample_rate,
                chunk_frames=settings.audio.chunk_frames,
            ),
        )
        mixer.add_source(
            "mic",
            MicrophoneSource(
                target_sample_rate=settings.audio.sample_rate,
                chunk_frames=settings.audio.chunk_frames,
                device_index=settings.audio.mic_device,
            ),
        )
        return mixer
    elif source_type == "wasapi":
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

    通过 stdout 输出 JSON Lines 消息（转写结果、摘要、状态），
    通过 stdin 接收控制命令（start/stop/switch_source）。
    所有日志输出到 stderr。

    生命周期：
    1. 启动 → stdout 输出 {"type": "status", "data": {"state": "ready"}}
    2. 收到 stdin control:start → 加载模型 → 启动 Pipeline → stdout RUNNING
    3. Pipeline 运行中 → stdout 输出 transcription / segment_summary / global_summary
    4. 收到 stdin control:stop → 停止 Pipeline → stdout STOPPED
    5. stdin EOF 或 SIGINT → 优雅退出
    """
    from backend.config import get_settings, load_llm_provider_config
    from backend.ipc.messages import (
        ControlAction,
        IPCMessage,
        MessageType,
        ProcessState,
    )
    from backend.ipc.subprocess_io import SubprocessReader, SubprocessWriter
    from backend.llm.client import CompanyLLMClient
    from backend.pipeline.meeting_pipeline import MeetingPipeline, TranscriptionEvent
    from backend.storage.meeting_store import MeetingStore
    from backend.storage.models import (
        ActionItemRecord,
        SummaryRecord,
        TranscriptionEntry,
    )
    from backend.summary.summary_coordinator import SummaryCoordinator

    logger = logging.getLogger(__name__)
    writer = SubprocessWriter()

    # 通知 Electron：子进程已就绪
    writer.write(IPCMessage.status(ProcessState.READY))

    pipeline = None
    summary_coordinator = None
    llm_client = None
    speaker_tracker = None  # 说话人跟踪器（需在 stop 时持久化）
    meeting_store = MeetingStore(get_settings().paths.data_dir)
    meeting_id: str | None = None
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
            speaker=event.speaker,
        )
        writer.write(msg)

    def _on_segment_summary(segment) -> None:
        """段落摘要回调 → 写 JSON Lines 到 stdout。"""
        msg = IPCMessage.segment_summary(
            time_range=segment.time_range,
            topics=segment.topics,
            conclusions=segment.conclusions,
            action_items=segment.action_items,
            raw_text=segment.raw_text,
        )
        writer.write(msg)

    def _on_global_summary(summary, action_items) -> None:
        """全局摘要回调 → 写 JSON Lines 到 stdout。"""
        items_data = [
            {"description": ai.description, "assignee": ai.assignee,
             "deadline": ai.deadline, "status": ai.status.value}
            for ai in action_items
        ]
        msg = IPCMessage.global_summary(
            raw_text=summary.raw_text,
            segments_merged=summary.segments_merged,
            merge_count=summary.merge_count,
            action_items=items_data,
        )
        writer.write(msg)

    def _create_llm_client():
        """从 llm_providers.yaml 创建 LLM 客户端。"""
        provider_cfg = load_llm_provider_config()
        return CompanyLLMClient(
            provider_cfg.settings,
            extra_headers=provider_cfg.extra_headers,
            verify_ssl=provider_cfg.ssl_verify,
        )

    # ── 存储回调 ─────────────────────────────────────────

    def _on_transcription_store(event: TranscriptionEvent) -> None:
        """转写回调 → 追加到 JSONL 文件。"""
        if meeting_id is None:
            return
        r = event.result
        entry = TranscriptionEntry(
            text=r.text,
            timestamp=event.timestamp,
            language=r.language,
            confidence=r.language_probability,
            speaker=event.speaker,
            segment_start=event.segment_start_time,
            segment_end=event.segment_end_time,
        )
        try:
            meeting_store.append_transcription(meeting_id, entry)
        except Exception:
            logger.exception("Failed to store transcription")

    def _on_segment_summary_store(segment) -> None:
        """段落摘要回调 → 保存到 summaries.json。"""
        if meeting_id is None:
            return
        record = SummaryRecord(
            summary_type="segment",
            raw_text=segment.raw_text,
            time_range=segment.time_range,
            topics=segment.topics,
            conclusions=segment.conclusions,
            action_items=segment.action_items,
        )
        try:
            meeting_store.add_segment_summary(meeting_id, record)
        except Exception:
            logger.exception("Failed to store segment summary")

    def _on_global_summary_store(summary, action_items) -> None:
        """全局摘要回调 → 保存到 summaries.json。"""
        if meeting_id is None:
            return
        global_record = SummaryRecord(
            summary_type="global",
            raw_text=summary.raw_text,
            segments_merged=summary.segments_merged,
            merge_count=summary.merge_count,
        )
        item_records = [
            ActionItemRecord(
                description=ai.description,
                assignee=ai.assignee,
                deadline=ai.deadline,
                status=ai.status.value if hasattr(ai.status, "value") else str(ai.status),
                source=ai.source,
            )
            for ai in action_items
        ]
        try:
            meeting_store.save_global_summary(meeting_id, global_record, item_records)
        except Exception:
            logger.exception("Failed to store global summary")

    async def _start_pipeline(src_type: str) -> MeetingPipeline:
        """创建并启动 Pipeline + SummaryCoordinator。"""
        nonlocal summary_coordinator, llm_client, meeting_id, speaker_tracker

        writer.write(IPCMessage.status(ProcessState.LOADING, message="Loading models..."))

        try:
            audio_src = _create_audio_source(src_type)
            vad = _create_vad()
            asr = _create_asr()
            asr.load()

            # 初始化说话人识别（可选）
            speaker_embedder = None
            speaker_tracker = None
            try:
                from backend.speaker.embedder import SpeakerEmbedder
                from backend.speaker.tracker import SpeakerTracker
                speaker_embedder = SpeakerEmbedder()
                speaker_tracker = SpeakerTracker()
                logger.info("Speaker diarization enabled.")
            except Exception:
                logger.warning("Speaker diarization unavailable, continuing without.", exc_info=True)

            pl = MeetingPipeline(
                audio_src, vad, asr,
                speaker_embedder=speaker_embedder,
                speaker_tracker=speaker_tracker,
            )
            pl.on_transcription(_on_transcription)

            # 初始化存储（创建会议文件夹）
            try:
                meeting_id = meeting_store.create_meeting(
                    title="", audio_source=src_type
                )
                pl.on_transcription(_on_transcription_store)
                logger.info("Meeting storage initialized: %s", meeting_id)
            except Exception:
                logger.exception("Failed to initialize meeting storage, continuing without persistence")
                meeting_id = None

            # 初始化总结模块（LLM 客户端 + 协调器）
            try:
                llm_client = _create_llm_client()
                summary_coordinator = SummaryCoordinator(llm_client)
                summary_coordinator.on_segment_summary(_on_segment_summary)
                summary_coordinator.on_global_summary(_on_global_summary)
                # 注册存储回调（在 IPC 回调之后，保持解耦）
                if meeting_id is not None:
                    summary_coordinator.on_segment_summary(_on_segment_summary_store)
                    summary_coordinator.on_global_summary(_on_global_summary_store)
                pl.on_transcription(summary_coordinator.feed_transcription)
                await summary_coordinator.start()
                logger.info("Summary coordinator initialized.")
            except Exception:
                logger.exception("Failed to initialize summary coordinator, continuing without summary")
                summary_coordinator = None

            await pl.start()

            writer.write(
                IPCMessage.status(
                    ProcessState.RUNNING,
                    source=src_type,
                    asr_model=asr._settings.model_size if hasattr(asr, "_settings") else "",
                    meeting_id=meeting_id or "",
                )
            )
            return pl
        except Exception as exc:
            logger.exception("Failed to start pipeline")
            writer.write(IPCMessage.error("pipeline_start_failed", str(exc)))
            raise

    async def _stop_pipeline() -> None:
        nonlocal pipeline, summary_coordinator, llm_client, meeting_id, speaker_tracker

        if summary_coordinator is not None:
            try:
                await summary_coordinator.stop()
            except Exception:
                logger.exception("Error stopping summary coordinator")
            summary_coordinator = None

        if llm_client is not None:
            try:
                await llm_client.close()
            except Exception:
                logger.exception("Error closing LLM client")
            llm_client = None

        if pipeline is not None:
            await pipeline.stop()
            pipeline = None

        # 发送 STOPPED 状态（携带 meeting_id 供前端保存摘要）
        stopped_meeting_id = meeting_id or ""
        writer.write(IPCMessage.status(ProcessState.STOPPED, meeting_id=stopped_meeting_id))

        # 持久化说话人跟踪状态
        if speaker_tracker is not None and meeting_id is not None:
            try:
                meeting_store.save_speakers(meeting_id, speaker_tracker.to_dict())
                logger.info("Speaker profiles saved for meeting %s", meeting_id)
            except Exception:
                logger.exception("Error saving speaker profiles")
        speaker_tracker = None

        if meeting_id is not None:
            try:
                meeting_store.finish_meeting(meeting_id)
                logger.info("Meeting finished: %s", meeting_id)
            except Exception:
                logger.exception("Error finishing meeting")
            meeting_id = None

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
            if new_source in ("wasapi", "mic", "both"):
                current_source_type = new_source
                asyncio.run_coroutine_threadsafe(_do_restart(new_source), main_loop)
            else:
                writer.write(
                    IPCMessage.error("invalid_source", f"Unknown source: {new_source}")
                )
        elif action == ControlAction.TRIGGER_SEGMENT_SUMMARY:
            asyncio.run_coroutine_threadsafe(_do_trigger_segment_summary(), main_loop)
        elif action == ControlAction.TRIGGER_GLOBAL_SUMMARY:
            asyncio.run_coroutine_threadsafe(_do_trigger_global_summary(), main_loop)
        elif action == ControlAction.SET_SUMMARY_INTERVAL:
            interval = message.data.get("interval_s")
            if isinstance(interval, (int, float)) and interval > 0:
                if summary_coordinator is not None:
                    summary_coordinator.set_summary_interval(float(interval))
                else:
                    logger.warning("Cannot set summary interval: coordinator not initialized")
            else:
                writer.write(IPCMessage.error("invalid_interval", f"Invalid interval: {interval}"))
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

    async def _do_trigger_segment_summary() -> None:
        if summary_coordinator is None:
            writer.write(IPCMessage.error("summary_unavailable", "Summary coordinator not initialized"))
            return
        try:
            await summary_coordinator.trigger_segment_summary()
        except Exception:
            logger.exception("Manual segment summary trigger failed")
            writer.write(IPCMessage.error("summary_error", "Failed to trigger segment summary"))

    async def _do_trigger_global_summary() -> None:
        if summary_coordinator is None:
            writer.write(IPCMessage.error("summary_unavailable", "Summary coordinator not initialized"))
            return
        try:
            await summary_coordinator.trigger_global_summary()
        except Exception:
            logger.exception("Manual global summary trigger failed")
            writer.write(IPCMessage.error("summary_error", "Failed to trigger global summary"))

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
