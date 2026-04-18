"""录制短音频并计算音量 — 供 Electron 调用测试设备是否工作。

用法:
  python -m backend.audio.test_device --type loopback --index 17 --seconds 3
  python -m backend.audio.test_device --type mic --index 1 --seconds 3

输出 JSON: { "ok": true, "peak": 0.05, "rms": 0.01, "duration": 3.0, "clipped": false }
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import numpy as np


def _test_loopback(device_index: int, seconds: float) -> dict:
    """测试 WASAPI loopback 设备。"""
    import pyaudiowpatch as pyaudio
    import threading

    pa = pyaudio.PyAudio()
    try:
        dev = pa.get_device_info_by_index(device_index)
        channels = dev["maxInputChannels"]
        rate = int(dev["defaultSampleRate"])
        frames_per_buffer = 512

        stream = pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=frames_per_buffer,
        )

        all_data = []
        stop_flag = threading.Event()

        def reader():
            while not stop_flag.is_set():
                try:
                    data = stream.read(frames_per_buffer, exception_on_overflow=False)
                    all_data.append(np.frombuffer(data, dtype=np.int16))
                except Exception:
                    break

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        time.sleep(seconds)
        stop_flag.set()
        t.join(timeout=1.0)

        stream.stop_stream()
        stream.close()

        if not all_data:
            return {"ok": True, "peak": 0, "rms": 0, "duration": seconds, "clipped": False, "hasSignal": False}

        audio = np.concatenate(all_data).astype(np.float32) / 32768.0
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)

        return _audio_stats(audio, rate)
    finally:
        pa.terminate()


def _test_mic(device_index: int, seconds: float) -> dict:
    """测试麦克风设备。"""
    import sounddevice as sd

    rate = 16000
    frames = int(rate * seconds)
    audio = sd.rec(frames, samplerate=rate, channels=1, dtype="float32",
                   device=device_index)
    sd.wait()
    audio = audio.flatten()
    return _audio_stats(audio, rate)


def _audio_stats(audio: np.ndarray, rate: int) -> dict:
    """计算音频统计。"""
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(audio ** 2)))
    duration = len(audio) / rate
    clipped = peak >= 0.99
    return {
        "ok": True,
        "peak": round(peak, 4),
        "rms": round(rms, 4),
        "duration": round(duration, 2),
        "clipped": clipped,
        "hasSignal": rms > 0.001,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", required=True, choices=["loopback", "mic"])
    parser.add_argument("--index", required=True, type=int)
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args()

    try:
        if args.type == "loopback":
            result = _test_loopback(args.index, args.seconds)
        else:
            result = _test_mic(args.index, args.seconds)
        json.dump(result, sys.stdout, ensure_ascii=False)
    except Exception as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
