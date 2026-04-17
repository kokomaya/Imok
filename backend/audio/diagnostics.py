"""音频采集自检工具 — 列举可用设备、验证采集能力。

单一职责：诊断音频环境，不包含采集逻辑本身。
可作为命令行脚本独立运行。
"""

from __future__ import annotations

import logging
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd

from backend.audio.base import AudioDeviceInfo

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticResult:
    """诊断结果。"""

    has_input_device: bool
    has_loopback_device: bool
    input_devices: list[AudioDeviceInfo]
    loopback_devices: list[AudioDeviceInfo]
    default_input: Optional[AudioDeviceInfo]
    errors: list[str]


def list_all_devices() -> list[AudioDeviceInfo]:
    """列出所有音频设备。"""
    devices: list[AudioDeviceInfo] = []
    host_apis = sd.query_hostapis()

    for dev in sd.query_devices():
        api_name = host_apis[dev["hostapi"]]["name"] if dev["hostapi"] < len(host_apis) else "Unknown"
        devices.append(
            AudioDeviceInfo(
                index=dev["index"] if "index" in dev else devices.__len__(),
                name=dev["name"],
                host_api=api_name,
                max_input_channels=dev["max_input_channels"],
                max_output_channels=dev["max_output_channels"],
                default_sample_rate=dev["default_samplerate"],
            )
        )
    return devices


def list_loopback_devices() -> list[AudioDeviceInfo]:
    """列出 WASAPI loopback 设备（需要 pyaudiowpatch）。"""
    loopback_devices: list[AudioDeviceInfo] = []
    try:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        try:
            for i in range(pa.get_device_count()):
                dev = pa.get_device_info_by_index(i)
                if dev.get("isLoopbackDevice"):
                    # 找到 WASAPI host API 名称
                    api_info = pa.get_host_api_info_by_index(dev["hostApi"])
                    loopback_devices.append(
                        AudioDeviceInfo(
                            index=i,
                            name=dev["name"],
                            host_api=api_info["name"],
                            max_input_channels=dev["maxInputChannels"],
                            max_output_channels=dev["maxOutputChannels"],
                            default_sample_rate=dev["defaultSampleRate"],
                            is_loopback=True,
                        )
                    )
        finally:
            pa.terminate()
    except ImportError:
        logger.warning("pyaudiowpatch not installed, loopback device detection skipped.")
    except Exception as exc:
        logger.warning("Failed to enumerate loopback devices: %s", exc)

    return loopback_devices


def run_diagnostics() -> DiagnosticResult:
    """执行完整音频环境诊断。"""
    errors: list[str] = []

    # 常规输入设备
    try:
        all_devices = list_all_devices()
        input_devices = [d for d in all_devices if d.max_input_channels > 0]
    except Exception as exc:
        errors.append(f"Failed to query audio devices: {exc}")
        input_devices = []

    # 默认输入设备
    default_input: Optional[AudioDeviceInfo] = None
    try:
        dev = sd.query_devices(kind="input")
        default_input = AudioDeviceInfo(
            index=0,
            name=dev["name"],
            host_api="",
            max_input_channels=dev["max_input_channels"],
            max_output_channels=dev["max_output_channels"],
            default_sample_rate=dev["default_samplerate"],
        )
    except Exception as exc:
        errors.append(f"No default input device: {exc}")

    # Loopback 设备
    loopback_devices = list_loopback_devices()
    if not loopback_devices:
        errors.append(
            "No WASAPI loopback devices found. "
            "WASAPI loopback requires pyaudiowpatch and a Windows audio output device."
        )

    return DiagnosticResult(
        has_input_device=len(input_devices) > 0,
        has_loopback_device=len(loopback_devices) > 0,
        input_devices=input_devices,
        loopback_devices=loopback_devices,
        default_input=default_input,
        errors=errors,
    )


def save_audio_to_wav(
    audio: np.ndarray,
    sample_rate: int,
    filepath: str | Path,
) -> Path:
    """将 float32 mono 音频保存为 16-bit WAV 文件。

    Args:
        audio: mono float32 ndarray, 值域 [-1, 1]。
        sample_rate: 采样率。
        filepath: 输出文件路径。

    Returns:
        保存的文件路径。
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # float32 → int16
    audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)

    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())

    logger.info("Saved %d samples (%.1f s) to %s", len(audio), len(audio) / sample_rate, filepath)
    return filepath


def print_diagnostic_report(result: DiagnosticResult) -> None:
    """打印诊断报告到标准输出。"""
    print("=" * 60)
    print("  Audio Diagnostics Report")
    print("=" * 60)

    print(f"\n  Default input device: ", end="")
    if result.default_input:
        print(f"{result.default_input.name} ({result.default_input.default_sample_rate:.0f} Hz)")
    else:
        print("NONE")

    print(f"\n  Input devices ({len(result.input_devices)}):")
    for d in result.input_devices:
        print(f"    [{d.index:2d}] {d.name} ({d.host_api}, {d.default_sample_rate:.0f} Hz, {d.max_input_channels} ch)")

    print(f"\n  Loopback devices ({len(result.loopback_devices)}):")
    for d in result.loopback_devices:
        print(f"    [{d.index:2d}] {d.name} ({d.default_sample_rate:.0f} Hz, {d.max_input_channels} ch)")

    if result.errors:
        print(f"\n  Warnings/Errors ({len(result.errors)}):")
        for err in result.errors:
            print(f"    ⚠ {err}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    diag = run_diagnostics()
    print_diagnostic_report(diag)
