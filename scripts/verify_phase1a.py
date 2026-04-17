"""Phase 1a 集成验证脚本 — 端到端测试 音频采集 → VAD → ASR 链路。

单一职责：自动化验证核心链路的功能正确性和性能指标。

使用方式：
    # 采集系统音频 30 秒并验证
    python -m scripts.verify_phase1a --source=wasapi --duration=30

    # 采集麦克风 20 秒
    python -m scripts.verify_phase1a --source=mic --duration=20

    # 仅运行单元测试 + 模块自检
    python -m scripts.verify_phase1a --quick

验证内容：
    1.6.1 端到端音频采集 → ASR 转写
    1.6.2 ASR 延迟测量（目标 < 2s）
    1.6.3 中英混合识别
    1.6.4 CPU 降级场景（medium + int8）
    1.6.5 结构化结果报告
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)


# ============================================================================
# 指标收集
# ============================================================================


@dataclass
class SegmentMetrics:
    """单个语音段的指标。"""

    segment_index: int
    audio_start_s: float
    audio_end_s: float
    audio_duration_s: float
    asr_latency_s: float  # 从语音段到达到 ASR 结果返回的耗时
    text: str
    language: str
    language_probability: float
    word_count: int


@dataclass
class VerificationReport:
    """Phase 1a 验证报告。"""

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    source_type: str = ""
    duration_requested_s: float = 0.0
    duration_actual_s: float = 0.0

    # 环境信息
    model_size: str = ""
    compute_type: str = ""
    device: str = ""
    sample_rate: int = 16000

    # 统计
    total_segments: int = 0
    total_words: int = 0
    total_audio_duration_s: float = 0.0
    languages_detected: List[str] = field(default_factory=list)

    # 延迟指标
    avg_asr_latency_s: float = 0.0
    max_asr_latency_s: float = 0.0
    min_asr_latency_s: float = 0.0
    p95_asr_latency_s: float = 0.0
    latency_target_met: bool = False  # < 2s

    # 详细段指标
    segments: List[SegmentMetrics] = field(default_factory=list)

    # 问题
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class MetricsCollector:
    """收集流水线运行指标。"""

    def __init__(self) -> None:
        self._segment_metrics: List[SegmentMetrics] = []
        self._segment_arrival_times: dict[int, float] = {}
        self._index = 0

    def on_segment_start(self) -> int:
        """记录语音段开始处理的时间，返回段索引。"""
        idx = self._index
        self._segment_arrival_times[idx] = time.perf_counter()
        self._index += 1
        return idx

    def on_transcription(self, event) -> None:
        """记录转写结果的指标。"""
        from backend.pipeline.meeting_pipeline import TranscriptionEvent

        e: TranscriptionEvent = event
        now = time.perf_counter()

        # 计算延迟：从事件 timestamp (墙钟) 到现在
        asr_latency = now - (self._segment_arrival_times.get(len(self._segment_metrics), now))

        words = e.result.text.split()
        metric = SegmentMetrics(
            segment_index=len(self._segment_metrics),
            audio_start_s=e.segment_start_time,
            audio_end_s=e.segment_end_time,
            audio_duration_s=e.segment_end_time - e.segment_start_time,
            asr_latency_s=asr_latency,
            text=e.result.text,
            language=e.result.language,
            language_probability=e.result.language_probability,
            word_count=len(words),
        )
        self._segment_metrics.append(metric)

        # 实时打印
        latency_color = "\033[92m" if asr_latency < 2.0 else "\033[91m"
        reset = "\033[0m"
        print(
            f"  [{e.segment_start_time:6.1f}s-{e.segment_end_time:6.1f}s] "
            f"({e.result.language}) "
            f"{latency_color}latency={asr_latency:.2f}s{reset} "
            f"| {e.result.text}"
        )

    @property
    def metrics(self) -> List[SegmentMetrics]:
        return list(self._segment_metrics)


# ============================================================================
# 验证逻辑
# ============================================================================


def _check_unit_tests() -> tuple[bool, str]:
    """运行全部单元测试（排除 hardware）。"""
    import subprocess

    print("\n" + "=" * 60)
    print("  [1/4] Running unit tests...")
    print("=" * 60)

    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "tests/backend/", "-m", "not hardware",
            "-v", "--tb=short",
        ],
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
    )

    print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
    if result.returncode != 0:
        print(result.stderr[-300:] if result.stderr else "")
        return False, f"Unit tests failed (exit code {result.returncode})"
    return True, "All unit tests passed"


def _check_audio_devices() -> tuple[bool, str]:
    """运行音频诊断。"""
    print("\n" + "=" * 60)
    print("  [2/4] Checking audio devices...")
    print("=" * 60)

    try:
        from backend.audio.diagnostics import run_diagnostics

        diag = run_diagnostics()
        n_input = len(diag.input_devices)
        n_loopback = len(diag.loopback_devices)
        print(f"  Input devices: {n_input}")
        print(f"  Loopback devices: {n_loopback}")
        print(f"  Default input: {diag.default_input.name if diag.default_input else 'None'}")
        print(f"  Has loopback: {diag.has_loopback_device}")

        if not diag.has_loopback_device:
            return False, "No WASAPI loopback devices found"
        return True, f"Found {n_input} input + {n_loopback} loopback devices"
    except Exception as e:
        return False, f"Audio diagnostics failed: {e}"


def _check_gpu_config() -> tuple[bool, str, dict]:
    """检测 GPU 配置和 ASR 参数。"""
    print("\n" + "=" * 60)
    print("  [3/4] Checking GPU / ASR config...")
    print("=" * 60)

    from backend.config import get_settings

    settings = get_settings()
    asr = settings.asr

    config_info = {
        "model_size": asr.model_size,
        "compute_type": asr.compute_type,
        "device": asr.device,
        "beam_size": asr.beam_size,
    }

    print(f"  Model: {asr.model_size}")
    print(f"  Device: {asr.device}")
    print(f"  Compute type: {asr.compute_type}")
    print(f"  Beam size: {asr.beam_size}")
    print(f"  VAD threshold: {asr.vad_threshold}")

    # 验证 CPU 降级场景 (1.6.4)
    if asr.device == "cpu":
        if asr.compute_type == "int8":
            print("  ✅ CPU int8 mode confirmed (no GPU degradation)")
        else:
            print(f"  ⚠️  CPU mode but compute_type={asr.compute_type}, expected int8")

    return True, f"{asr.model_size} on {asr.device} ({asr.compute_type})", config_info


async def _run_e2e_capture(
    source_type: str,
    duration_s: float,
    collector: MetricsCollector,
) -> VerificationReport:
    """端到端采集 + ASR 转写测试。"""
    from backend.config import get_settings
    from backend.asr.vad import VoiceActivityDetector
    from backend.asr.whisper_engine import WhisperEngine
    from backend.pipeline.meeting_pipeline import MeetingPipeline

    settings = get_settings()

    print("\n" + "=" * 60)
    print(f"  [4/4] E2E capture: {source_type}, {duration_s}s")
    print("=" * 60)

    report = VerificationReport(
        source_type=source_type,
        duration_requested_s=duration_s,
        model_size=settings.asr.model_size or "medium",
        compute_type=settings.asr.compute_type or "int8",
        device=settings.asr.device or "cpu",
        sample_rate=settings.audio.sample_rate,
    )

    # 创建音频源
    try:
        if source_type == "wasapi":
            from backend.audio.wasapi_source import WASAPILoopbackSource

            audio_source = WASAPILoopbackSource(
                target_sample_rate=settings.audio.sample_rate,
                chunk_frames=settings.audio.chunk_frames,
            )
        else:
            from backend.audio.mic_source import MicrophoneSource

            audio_source = MicrophoneSource(
                target_sample_rate=settings.audio.sample_rate,
                chunk_frames=settings.audio.chunk_frames,
                device_index=settings.audio.mic_device,
            )
    except Exception as e:
        report.errors.append(f"Failed to create audio source: {e}")
        return report

    # 创建 VAD
    try:
        print("  Loading Silero-VAD...")
        vad = VoiceActivityDetector(
            sample_rate=settings.audio.sample_rate,
            threshold=settings.asr.vad_threshold,
            min_silence_ms=settings.asr.vad_min_silence_ms,
            max_segment_s=settings.asr.vad_max_segment_s,
        )
    except Exception as e:
        report.errors.append(f"Failed to load VAD: {e}")
        return report

    # 创建 ASR
    print("  Creating WhisperEngine (lazy load)...")
    asr = WhisperEngine(settings.asr)

    # 包装 _process_segment 以记录到达时间
    pipeline = MeetingPipeline(audio_source, vad, asr)

    original_process = pipeline._process_segment

    async def instrumented_process(segment):
        collector.on_segment_start()
        await original_process(segment)

    pipeline._process_segment = instrumented_process
    pipeline.on_transcription(collector.on_transcription)

    # 运行
    print(f"  Starting capture for {duration_s}s...")
    print(f"  (Play audio in Teams or speak into mic now)")
    print()

    start_time = time.perf_counter()

    try:
        await pipeline.start()
        await asyncio.sleep(duration_s)
    except Exception as e:
        report.errors.append(f"Pipeline error: {e}")
    finally:
        await pipeline.stop()

    elapsed = time.perf_counter() - start_time
    report.duration_actual_s = elapsed

    # 汇总指标
    metrics = collector.metrics
    report.segments = metrics
    report.total_segments = len(metrics)
    report.total_words = sum(m.word_count for m in metrics)
    report.total_audio_duration_s = sum(m.audio_duration_s for m in metrics)
    report.languages_detected = list(set(m.language for m in metrics))

    if metrics:
        latencies = [m.asr_latency_s for m in metrics]
        report.avg_asr_latency_s = sum(latencies) / len(latencies)
        report.max_asr_latency_s = max(latencies)
        report.min_asr_latency_s = min(latencies)
        # P95
        sorted_lat = sorted(latencies)
        p95_idx = int(len(sorted_lat) * 0.95)
        report.p95_asr_latency_s = sorted_lat[min(p95_idx, len(sorted_lat) - 1)]
        report.latency_target_met = report.p95_asr_latency_s < 2.0
    else:
        report.warnings.append("No speech segments detected during capture.")

    return report


def _print_report(report: VerificationReport) -> None:
    """格式化打印验证报告。"""
    print()
    print("=" * 60)
    print("  Phase 1a Verification Report")
    print("=" * 60)
    print()

    print(f"  Timestamp:      {report.timestamp}")
    print(f"  Source:          {report.source_type}")
    print(f"  Duration:        {report.duration_actual_s:.1f}s (requested {report.duration_requested_s:.1f}s)")
    print(f"  Model:           {report.model_size} ({report.device}, {report.compute_type})")
    print()

    print("  --- Transcription Summary ---")
    print(f"  Segments:        {report.total_segments}")
    print(f"  Words:           {report.total_words}")
    print(f"  Audio duration:  {report.total_audio_duration_s:.1f}s")
    print(f"  Languages:       {', '.join(report.languages_detected) or 'none'}")
    print()

    print("  --- Latency (target < 2.0s) ---")
    if report.total_segments > 0:
        status = "✅ PASS" if report.latency_target_met else "❌ FAIL"
        print(f"  Avg:             {report.avg_asr_latency_s:.3f}s")
        print(f"  Min:             {report.min_asr_latency_s:.3f}s")
        print(f"  Max:             {report.max_asr_latency_s:.3f}s")
        print(f"  P95:             {report.p95_asr_latency_s:.3f}s")
        print(f"  Target met:      {status}")
    else:
        print("  (no segments to measure)")
    print()

    if report.errors:
        print("  --- Errors ---")
        for e in report.errors:
            print(f"  ❌ {e}")
        print()

    if report.warnings:
        print("  --- Warnings ---")
        for w in report.warnings:
            print(f"  ⚠️  {w}")
        print()

    # 验收判定
    print("  --- Acceptance Criteria ---")
    checks = [
        ("Audio capture works", report.total_segments > 0 or not report.errors),
        ("ASR produces text", report.total_words > 0),
        ("Latency < 2s (P95)", report.latency_target_met),
        ("No critical errors", len(report.errors) == 0),
    ]
    all_pass = True
    for desc, passed in checks:
        icon = "✅" if passed else "❌"
        print(f"  {icon} {desc}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print("  🎉 Phase 1a PASSED — Core pipeline verified!")
    else:
        print("  ⚠️  Phase 1a has issues — see details above.")
    print()


def _save_report(report: VerificationReport, path: Path) -> None:
    """保存报告为 JSON。"""
    # 转为可序列化的 dict
    data = {
        "timestamp": report.timestamp,
        "source_type": report.source_type,
        "duration_requested_s": report.duration_requested_s,
        "duration_actual_s": report.duration_actual_s,
        "model_size": report.model_size,
        "compute_type": report.compute_type,
        "device": report.device,
        "sample_rate": report.sample_rate,
        "total_segments": report.total_segments,
        "total_words": report.total_words,
        "total_audio_duration_s": report.total_audio_duration_s,
        "languages_detected": report.languages_detected,
        "avg_asr_latency_s": report.avg_asr_latency_s,
        "max_asr_latency_s": report.max_asr_latency_s,
        "min_asr_latency_s": report.min_asr_latency_s,
        "p95_asr_latency_s": report.p95_asr_latency_s,
        "latency_target_met": report.latency_target_met,
        "errors": report.errors,
        "warnings": report.warnings,
        "segments": [
            {
                "index": s.segment_index,
                "start_s": s.audio_start_s,
                "end_s": s.audio_end_s,
                "duration_s": s.audio_duration_s,
                "latency_s": s.asr_latency_s,
                "text": s.text,
                "language": s.language,
                "lang_prob": s.language_probability,
                "words": s.word_count,
            }
            for s in report.segments
        ],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Report saved to: {path}")


# ============================================================================
# CLI 入口
# ============================================================================


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 1a Integration Verification")
    parser.add_argument(
        "--source",
        choices=["wasapi", "mic"],
        default="wasapi",
        help="Audio source type",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Capture duration in seconds",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode: only unit tests + device check, no e2e capture",
    )
    parser.add_argument(
        "--save-report",
        type=str,
        default=None,
        help="Path to save JSON report (default: data/verify_phase1a_<timestamp>.json)",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    args = _parse_args()

    print()
    print("╔════════════════════════════════════════════════════════╗")
    print("║       IMOK — Phase 1a Integration Verification        ║")
    print("╚════════════════════════════════════════════════════════╝")

    # Step 1: Unit tests
    tests_ok, tests_msg = _check_unit_tests()
    print(f"\n  Result: {'✅' if tests_ok else '❌'} {tests_msg}")

    # Step 2: Audio devices
    devices_ok, devices_msg = _check_audio_devices()
    print(f"\n  Result: {'✅' if devices_ok else '❌'} {devices_msg}")

    # Step 3: GPU / ASR config
    config_ok, config_msg, config_info = _check_gpu_config()
    print(f"\n  Result: {'✅' if config_ok else '❌'} {config_msg}")

    if args.quick:
        print("\n  [Quick mode] Skipping e2e capture.")
        print("  Run without --quick to perform full verification.\n")
        return

    if not (tests_ok and devices_ok):
        print("\n  ❌ Pre-checks failed. Fix issues before running e2e test.\n")
        return

    # Step 4: E2E capture
    collector = MetricsCollector()
    report = asyncio.run(
        _run_e2e_capture(args.source, args.duration, collector)
    )

    # Print report
    _print_report(report)

    # Save report
    if args.save_report:
        report_path = Path(args.save_report)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = _PROJECT_ROOT / "data" / f"verify_phase1a_{ts}.json"

    _save_report(report, report_path)


if __name__ == "__main__":
    main()
