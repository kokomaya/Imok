"""音频采集模块单元测试。

测试内容：
- resampler 工具函数
- MicrophoneSource 基本采集（5 秒，保存 WAV）
- WASAPILoopbackSource 基本采集（5 秒，保存 WAV）
- 诊断工具函数

注意：涉及真实硬件的测试标记为 @pytest.mark.hardware，
CI 环境中可通过 `pytest -m "not hardware"` 跳过。
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from backend.audio.base import AudioChunk, AudioSourceType
from backend.audio.resampler import resample_audio, to_mono_float32
from backend.audio.diagnostics import run_diagnostics, save_audio_to_wav


# ============================================================================
# resampler 测试
# ============================================================================


class TestResampleAudio:
    def test_same_rate_passthrough(self):
        audio = np.random.randn(16000).astype(np.float32)
        result = resample_audio(audio, 16000, 16000)
        np.testing.assert_array_equal(result, audio)

    def test_downsample_48k_to_16k(self):
        # 1 秒 48kHz → 应产生 ~16000 样本
        audio = np.random.randn(48000).astype(np.float32)
        result = resample_audio(audio, 48000, 16000)
        assert result.dtype == np.float32
        assert abs(len(result) - 16000) <= 1

    def test_upsample_16k_to_48k(self):
        audio = np.random.randn(16000).astype(np.float32)
        result = resample_audio(audio, 16000, 48000)
        assert abs(len(result) - 48000) <= 1

    def test_44100_to_16000(self):
        audio = np.random.randn(44100).astype(np.float32)
        result = resample_audio(audio, 44100, 16000)
        expected = int(44100 * 16000 / 44100)
        assert abs(len(result) - expected) <= 10  # resample_poly may add small padding


class TestToMonoFloat32:
    def test_int16_to_float32(self):
        audio = np.array([0, 16384, -16384, 32767, -32768], dtype=np.int16)
        result = to_mono_float32(audio)
        assert result.dtype == np.float32
        assert abs(result[0]) < 1e-6
        assert abs(result[1] - 0.5) < 0.01
        assert abs(result[3] - 1.0) < 0.01

    def test_stereo_to_mono(self):
        stereo = np.array([[0.5, -0.5], [1.0, 0.0]], dtype=np.float32)
        result = to_mono_float32(stereo)
        assert result.ndim == 1
        assert len(result) == 2
        assert abs(result[0] - 0.0) < 1e-6  # mean(0.5, -0.5)
        assert abs(result[1] - 0.5) < 1e-6  # mean(1.0, 0.0)

    def test_already_mono_float32(self):
        audio = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        result = to_mono_float32(audio)
        np.testing.assert_array_equal(result, audio)


# ============================================================================
# AudioChunk 测试
# ============================================================================


class TestAudioChunk:
    def test_duration_calculation(self):
        data = np.zeros(16000, dtype=np.float32)
        chunk = AudioChunk(data=data, sample_rate=16000, timestamp_s=0.0)
        assert abs(chunk.duration_s - 1.0) < 1e-6

    def test_source_type_default(self):
        data = np.zeros(100, dtype=np.float32)
        chunk = AudioChunk(data=data, sample_rate=16000, timestamp_s=0.0)
        assert chunk.source_type == AudioSourceType.MICROPHONE


# ============================================================================
# diagnostics 测试
# ============================================================================


class TestDiagnostics:
    def test_run_diagnostics_returns_result(self):
        result = run_diagnostics()
        assert isinstance(result.input_devices, list)
        assert isinstance(result.loopback_devices, list)
        assert isinstance(result.errors, list)

    def test_save_audio_to_wav(self, tmp_path: Path):
        audio = np.random.randn(16000).astype(np.float32) * 0.5
        filepath = tmp_path / "test_output.wav"
        result_path = save_audio_to_wav(audio, 16000, filepath)
        assert result_path.exists()
        assert result_path.stat().st_size > 0


# ============================================================================
# 硬件测试 — 需要真实音频设备
# ============================================================================

hardware = pytest.mark.skipif(
    not run_diagnostics().has_input_device,
    reason="No input audio device available",
)


@hardware
class TestMicrophoneSourceHardware:
    """麦克风采集测试 — 采集 5 秒并保存 WAV。"""

    def test_capture_5_seconds(self, tmp_path: Path):
        from backend.audio.mic_source import MicrophoneSource

        source = MicrophoneSource(target_sample_rate=16000, chunk_frames=512)
        collected: list[np.ndarray] = []

        source.start()
        assert source.is_active

        start = time.monotonic()
        while time.monotonic() - start < 5.0:
            chunk = source.read_chunk()
            if chunk is not None:
                collected.append(chunk.data)
                assert chunk.sample_rate == 16000
                assert chunk.data.dtype == np.float32

        source.stop()
        assert not source.is_active

        if collected:
            full_audio = np.concatenate(collected)
            wav_path = tmp_path / "mic_5s.wav"
            save_audio_to_wav(full_audio, 16000, wav_path)
            assert wav_path.exists()
            duration = len(full_audio) / 16000
            assert duration >= 4.0  # allow some tolerance
            print(f"Captured {duration:.1f}s from microphone → {wav_path}")


loopback_available = pytest.mark.skipif(
    not run_diagnostics().has_loopback_device,
    reason="No WASAPI loopback device available",
)


@loopback_available
class TestWASAPILoopbackSourceHardware:
    """WASAPI Loopback 采集测试 — 采集 5 秒并保存 WAV。"""

    def test_capture_5_seconds(self, tmp_path: Path):
        from backend.audio.wasapi_source import WASAPILoopbackSource

        source = WASAPILoopbackSource(target_sample_rate=16000, chunk_frames=512)
        collected: list[np.ndarray] = []

        source.start()
        assert source.is_active

        start = time.monotonic()
        while time.monotonic() - start < 5.0:
            chunk = source.read_chunk()
            if chunk is not None:
                collected.append(chunk.data)
                assert chunk.sample_rate == 16000

        source.stop()
        assert not source.is_active

        if collected:
            full_audio = np.concatenate(collected)
            wav_path = tmp_path / "loopback_5s.wav"
            save_audio_to_wav(full_audio, 16000, wav_path)
            assert wav_path.exists()
            duration = len(full_audio) / 16000
            print(f"Captured {duration:.1f}s from loopback → {wav_path}")


@hardware
class TestMicrophoneSourceContextManager:
    """测试 context manager 用法。"""

    def test_with_statement(self):
        from backend.audio.mic_source import MicrophoneSource

        source = MicrophoneSource(target_sample_rate=16000)
        with source:
            assert source.is_active
            chunk = source.read_chunk()
            # 可能返回 None（设备刚启动），但不应报错

        assert not source.is_active

    def test_start_stop_idempotent(self):
        from backend.audio.mic_source import MicrophoneSource

        source = MicrophoneSource(target_sample_rate=16000)
        source.start()
        source.start()  # 重复 start 不报错
        assert source.is_active
        source.stop()
        source.stop()  # 重复 stop 不报错
        assert not source.is_active
