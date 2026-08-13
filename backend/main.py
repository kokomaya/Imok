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


def _create_audio_source(source_type: str, *, loopback_device=None, mic_device=None):
    """创建音频源 — 委托到 pipeline.factory。"""
    from backend.pipeline.factory import create_audio_source
    return create_audio_source(source_type, loopback_device=loopback_device, mic_device=mic_device)


def _create_vad():
    """创建 VAD 实例 — 委托到 pipeline.factory。"""
    from backend.pipeline.factory import create_vad
    return create_vad()


def _create_asr():
    """创建 ASR 引擎 — 委托到 pipeline.factory。"""
    from backend.pipeline.factory import create_asr
    return create_asr()


# ASR/VAD 参数取值范围（超出则钳制，非法则忽略该项）
_ASR_MODEL_SIZES = {"tiny", "base", "small", "medium", "large-v2", "large-v3"}
_ASR_COMPUTE_TYPES = {"int8", "int8_float16", "float16", "float32"}
_ASR_DEVICES = {"cpu", "cuda"}


def _apply_asr_config(data: dict) -> None:
    """把前端下发的 ASR/VAD 参数应用到全局配置单例。

    参数在下一次 _start_pipeline() 新建 VAD/ASR 时生效（不影响进行中的会议）。
    model_size / compute_type / device 收到 "auto"/"" 视为自动检测（None → resolve_with_gpu）。
    非法值忽略，越界值钳制，保证鲁棒。
    """
    from backend.config import get_settings

    logger = logging.getLogger(__name__)
    update: dict = {}

    def _clamp(name: str, lo: float, hi: float, cast):
        if name in data and data[name] is not None:
            try:
                update[name] = max(lo, min(hi, cast(data[name])))
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid ASR config %s=%r", name, data[name])

    _clamp("vad_max_segment_s", 3.0, 30.0, float)
    _clamp("vad_min_silence_ms", 100, 2000, int)
    _clamp("vad_threshold", 0.1, 0.9, float)
    _clamp("beam_size", 1, 10, int)

    if "word_timestamps" in data and data["word_timestamps"] is not None:
        update["word_timestamps"] = bool(data["word_timestamps"])

    # 枚举/自动型字段：auto/空 → None（走 GPU 自动检测）
    needs_resolve = False
    for name, allowed in (
        ("model_size", _ASR_MODEL_SIZES),
        ("compute_type", _ASR_COMPUTE_TYPES),
        ("device", _ASR_DEVICES),
    ):
        if name in data:
            val = data[name]
            if val in (None, "", "auto"):
                update[name] = None
                needs_resolve = True
            elif val in allowed:
                update[name] = val
            else:
                logger.warning("Ignoring invalid ASR config %s=%r", name, val)

    if "language" in data:
        val = data["language"]
        update["language"] = None if val in (None, "", "auto") else str(val)

    if not update:
        return

    app = get_settings()
    new_asr = app.asr.model_copy(update=update)
    # 有字段被重置为 auto(None)，需要重新做 GPU 检测回填 model/compute/device
    if needs_resolve:
        new_asr = new_asr.resolve_with_gpu()
    app.asr = new_asr
    logger.info(
        "ASR config applied (effective next start): model=%s device=%s compute=%s "
        "beam=%d word_ts=%s vad[max=%.1fs min_silence=%dms thr=%.2f] lang=%s",
        new_asr.model_size, new_asr.device, new_asr.compute_type,
        new_asr.beam_size, new_asr.word_timestamps,
        new_asr.vad_max_segment_s, new_asr.vad_min_silence_ms,
        new_asr.vad_threshold, new_asr.language or "auto",
    )


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
    from backend.audio.recorder import AudioRecorder
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
    _pipeline_lock = asyncio.Lock()
    summary_coordinator = None
    llm_client = None
    speaker_tracker = None  # 说话人跟踪器（需在 stop 时持久化）
    meeting_store = MeetingStore(get_settings().paths.data_dir)
    meeting_id: str | None = None
    stop_event = asyncio.Event()
    current_source_type = source_type
    # 用户选择的音频设备索引（None = 自动/默认）
    selected_loopback_device: int | None = None
    selected_mic_device: int | None = None
    # 录音状态（整场会议共享一个录音器，跨音频源切换保持连续）
    recorder = None  # type: Optional[AudioRecorder]
    recording_enabled = False
    recording_mode = "silence"  # 'silence' | 'skip'
    # 自动摘要设置（跨会议保持，会议启动时应用到新协调器）
    summary_auto_enabled = False  # 默认手动摘要；由前端下发开启定时
    summary_interval_s: float | None = None
    # 自定义总结模板（跨会议保持）：{segment_system, merge_system, section_titles}
    summary_template: dict | None = None
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
            source=event.source_name,
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

    async def _start_pipeline(src_type: str, *, resume_meeting_id: str = "") -> MeetingPipeline:
        """创建并启动 Pipeline + SummaryCoordinator。

        当 src_type == 'both' 时，为每个音频源创建独立的 VAD 实例，
        各自独立检测语音，共享同一个 ASR 引擎。
        """
        nonlocal summary_coordinator, llm_client, meeting_id, speaker_tracker, recorder

        writer.write(IPCMessage.status(ProcessState.LOADING, message="Loading models..."))

        try:
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
                # 继续会议时恢复上次的说话人档案
                if resume_meeting_id:
                    saved = meeting_store.load_speakers(resume_meeting_id)
                    if saved and saved.get("speakers"):
                        speaker_tracker = SpeakerTracker.from_dict(saved)
                        logger.info("Restored %d speaker profiles from previous meeting.", len(saved["speakers"]))
                    else:
                        speaker_tracker = SpeakerTracker()
                else:
                    speaker_tracker = SpeakerTracker()
                logger.info("Speaker diarization enabled.")
            except Exception:
                logger.warning("Speaker diarization unavailable, continuing without.", exc_info=True)

            extra_sources = None

            if src_type == "both":
                # 多源模式 — 各源独立 VAD，不再混合
                from backend.audio.wasapi_source import WASAPILoopbackSource
                from backend.audio.mic_source import MicrophoneSource
                from backend.config import get_settings

                settings = get_settings()
                lb_idx = selected_loopback_device if selected_loopback_device is not None else settings.audio.loopback_device
                mic_idx = selected_mic_device if selected_mic_device is not None else settings.audio.mic_device

                main_source = WASAPILoopbackSource(
                    target_sample_rate=settings.audio.sample_rate,
                    chunk_frames=settings.audio.chunk_frames,
                    device_index=lb_idx,
                )
                mic_source = MicrophoneSource(
                    target_sample_rate=settings.audio.sample_rate,
                    chunk_frames=settings.audio.chunk_frames,
                    device_index=mic_idx,
                )
                mic_vad = _create_vad()
                extra_sources = [("mic", mic_source, mic_vad)]
            else:
                main_source = _create_audio_source(
                    src_type,
                    loopback_device=selected_loopback_device,
                    mic_device=selected_mic_device,
                )

            pl = MeetingPipeline(
                main_source, vad, asr,
                speaker_embedder=speaker_embedder,
                speaker_tracker=speaker_tracker,
                extra_sources=extra_sources,
            )
            pl.on_transcription(_on_transcription)

            # 注册音频电平回调 → IPC 实时推送
            def _on_audio_level(levels):
                writer.write(IPCMessage.audio_level(levels))

            pl.on_audio_level(_on_audio_level)

            # 初始化存储（创建新会议或恢复已有会议）
            try:
                if resume_meeting_id:
                    meeting_id = meeting_store.resume_meeting(resume_meeting_id)
                    logger.info("Resuming meeting: %s", meeting_id)
                else:
                    meeting_id = meeting_store.create_meeting(
                        title="", audio_source=src_type
                    )
                    logger.info("Meeting storage initialized: %s", meeting_id)
                pl.on_transcription(_on_transcription_store)
            except Exception:
                logger.exception("Failed to initialize meeting storage, continuing without persistence")
                meeting_id = None

            # 录音器：整场会议共享一个，跨音源切换保持连续（首次启用时创建）
            if meeting_id is not None:
                if recorder is None:
                    try:
                        rec_path = meeting_store.get_meeting_dir(meeting_id) / "recording.wav"
                        recorder = AudioRecorder(rec_path, get_settings().audio.sample_rate)
                        recorder.set_mute_mode(recording_mode)
                        recorder.set_muted(not recording_enabled)
                        if recording_enabled:
                            recorder.open()
                    except Exception:
                        logger.exception("Failed to initialize recorder")
                        recorder = None
                if recorder is not None:
                    pl.on_audio(recorder.feed)

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
                # 应用跨会议保持的自动摘要设置
                if summary_interval_s is not None:
                    summary_coordinator.set_summary_interval(summary_interval_s)
                summary_coordinator.set_auto_summary(summary_auto_enabled)
                # 应用跨会议保持的自定义总结模板
                if summary_template is not None:
                    summary_coordinator.set_summary_template(
                        summary_template.get("segment_system", ""),
                        summary_template.get("merge_system", ""),
                        summary_template.get("section_titles"),
                    )
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
                    asr_model=(
                        f"{asr._model_size} ({asr._device})"
                        if hasattr(asr, "_model_size") else ""
                    ),
                    meeting_id=meeting_id or "",
                )
            )
            return pl
        except Exception as exc:
            logger.exception("Failed to start pipeline")
            writer.write(IPCMessage.error("pipeline_start_failed", str(exc)))
            raise

    async def _finalize_summary(sc, lc) -> None:
        """后台完成摘要合并和 LLM 客户端关闭（不阻塞 UI）。"""
        if sc is not None:
            try:
                await sc.stop()
            except Exception:
                logger.exception("Error stopping summary coordinator (background)")
        if lc is not None:
            try:
                await lc.close()
            except Exception:
                logger.exception("Error closing LLM client (background)")
        logger.info("Background summary finalization completed.")

    async def _stop_pipeline() -> None:
        nonlocal pipeline, summary_coordinator, llm_client, meeting_id, speaker_tracker
        # 没有运行中的资源时，跳过（避免向前端发送无意义的 STOPPING/STOPPED）
        if pipeline is None and summary_coordinator is None and meeting_id is None:
            return
        # 立即通知前端“正在停止”，避免 UI 无反馈
        stopped_meeting_id = meeting_id or ""
        writer.write(IPCMessage.status(ProcessState.STOPPING, meeting_id=stopped_meeting_id))

        # 停止音频采集（停源 + flush VAD + drain ASR）
        if pipeline is not None:
            await pipeline.stop()
            pipeline = None

        # 持久化说话人跟踪状态（快，纯本地文件）
        if speaker_tracker is not None and stopped_meeting_id:
            try:
                meeting_store.save_speakers(stopped_meeting_id, speaker_tracker.to_dict())
                logger.info("Speaker profiles saved for meeting %s", stopped_meeting_id)
            except Exception:
                logger.exception("Error saving speaker profiles")
        speaker_tracker = None

        if stopped_meeting_id:
            try:
                meeting_store.finish_meeting(stopped_meeting_id)
                logger.info("Meeting finished: %s", stopped_meeting_id)
            except Exception:
                logger.exception("Error finishing meeting")
        meeting_id = None

        # 先发 STOPPED，让前端立刻结束等待
        writer.write(IPCMessage.status(ProcessState.STOPPED, meeting_id=stopped_meeting_id))

        # 摘要协调器收尾（涉及 LLM 调用，可能 5-20s）放后台执行
        # 摘要结果仍通过现有 IPC 回调推送给前端
        sc, lc = summary_coordinator, llm_client
        summary_coordinator = None
        llm_client = None
        if sc is not None or lc is not None:
            # 卸载文件存储回调，防止后台任务写入新会议的 meeting_id
            if sc is not None:
                sc._segment_callbacks = [
                    cb for cb in sc._segment_callbacks
                    if cb is not _on_segment_summary_store
                ]
                sc._global_callbacks = [
                    cb for cb in sc._global_callbacks
                    if cb is not _on_global_summary_store
                ]
            asyncio.create_task(_finalize_summary(sc, lc))

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
                resume_mid = message.data.get("meeting_id", "")
                asyncio.run_coroutine_threadsafe(
                    _do_restart(new_source, resume_meeting_id=resume_mid), main_loop
                )
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
                nonlocal summary_interval_s
                summary_interval_s = float(interval)
                if summary_coordinator is not None:
                    summary_coordinator.set_summary_interval(float(interval))
                else:
                    logger.warning("Cannot set summary interval: coordinator not initialized")
            else:
                writer.write(IPCMessage.error("invalid_interval", f"Invalid interval: {interval}"))
        elif action == ControlAction.SET_AUTO_SUMMARY:
            nonlocal summary_auto_enabled
            summary_auto_enabled = bool(message.data.get("enabled", True))
            if summary_coordinator is not None:
                summary_coordinator.set_auto_summary(summary_auto_enabled)
            logger.info("Auto summary set to %s", summary_auto_enabled)
        elif action == ControlAction.SET_SUMMARY_TEMPLATE:
            nonlocal summary_template
            summary_template = {
                "segment_system": str(message.data.get("segment_system", "") or ""),
                "merge_system": str(message.data.get("merge_system", "") or ""),
                "section_titles": message.data.get("section_titles") or {},
            }
            if summary_coordinator is not None:
                summary_coordinator.set_summary_template(
                    summary_template["segment_system"],
                    summary_template["merge_system"],
                    summary_template["section_titles"],
                )
            logger.info("Summary template updated via control")
        elif action == ControlAction.SET_DEVICES:
            nonlocal selected_loopback_device, selected_mic_device
            lb = message.data.get("loopback_device")
            mic = message.data.get("mic_device")
            selected_loopback_device = int(lb) if lb is not None else None
            selected_mic_device = int(mic) if mic is not None else None
            logger.info("Device selection updated: loopback=%s, mic=%s",
                        selected_loopback_device, selected_mic_device)
        elif action == ControlAction.SET_RECORDING:
            nonlocal recording_enabled, recording_mode
            enabled = bool(message.data.get("recording", False))
            mode = message.data.get("mode", recording_mode)
            recording_enabled = enabled
            if mode in ("silence", "skip"):
                recording_mode = mode
            # 应用到当前录音器（若会议进行中）
            if recorder is not None:
                recorder.set_mute_mode(recording_mode)
                if enabled and not recorder.is_open:
                    recorder.open()
                recorder.set_muted(not enabled)
            logger.info("Recording set: enabled=%s mode=%s", recording_enabled, recording_mode)
        elif action == ControlAction.SET_ASR_CONFIG:
            _apply_asr_config(message.data)
        else:
            logger.warning("Unknown control action: %s", action)

    async def _do_start() -> None:
        nonlocal pipeline
        async with _pipeline_lock:
            if pipeline is not None:
                return
            try:
                pipeline = await _start_pipeline(current_source_type)
            except Exception:
                pass  # 错误已在 _start_pipeline 中通过 IPC 报告

    async def _do_stop() -> None:
        nonlocal recorder
        async with _pipeline_lock:
            await _stop_pipeline()
            # 会议结束：关闭录音器（音频源切换走 _do_restart，不会到这里）
            if recorder is not None:
                try:
                    recorder.close()
                except Exception:
                    logger.exception("Error closing recorder")
                recorder = None

    async def _do_restart(new_source: str, *, resume_meeting_id: str = "") -> None:
        nonlocal pipeline
        async with _pipeline_lock:
            await _stop_pipeline()
            try:
                pipeline = await _start_pipeline(new_source, resume_meeting_id=resume_meeting_id)
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
        if recorder is not None:
            try:
                recorder.close()
            except Exception:
                logger.exception("Error closing recorder on shutdown")
            recorder = None
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
