"""列举音频设备 — 输出 JSON 到 stdout，供 Electron 主进程调用。

用法：python -m backend.audio.list_devices
输出：{ "loopback": [...], "input": [...] }
"""

from __future__ import annotations

import json
import sys


def _list_input_devices() -> list[dict]:
    """列出所有输入设备（sounddevice）。"""
    devices = []
    try:
        import sounddevice as sd

        host_apis = sd.query_hostapis()
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] <= 0:
                continue
            api_name = (
                host_apis[dev["hostapi"]]["name"]
                if dev["hostapi"] < len(host_apis)
                else "Unknown"
            )
            devices.append({
                "index": idx,
                "name": dev["name"],
                "hostApi": api_name,
                "maxInputChannels": dev["max_input_channels"],
                "sampleRate": dev["default_samplerate"],
                "isLoopback": False,
                "isDefault": idx == sd.default.device[0],
            })
    except Exception as exc:
        print(f"[list_devices] input error: {exc}", file=sys.stderr)
    return devices


def _list_loopback_devices() -> list[dict]:
    """列出 WASAPI loopback 设备（pyaudiowpatch）。"""
    devices = []
    try:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        try:
            for i in range(pa.get_device_count()):
                dev = pa.get_device_info_by_index(i)
                if not dev.get("isLoopbackDevice"):
                    continue
                api_info = pa.get_host_api_info_by_index(dev["hostApi"])
                devices.append({
                    "index": i,
                    "name": dev["name"],
                    "hostApi": api_info["name"],
                    "maxInputChannels": dev["maxInputChannels"],
                    "sampleRate": dev["defaultSampleRate"],
                    "isLoopback": True,
                    "isDefault": False,
                })
        finally:
            pa.terminate()

        # 标记默认输出对应的 loopback 为 default
        try:
            pa2 = pyaudio.PyAudio()
            try:
                wasapi_idx = None
                for api_idx in range(pa2.get_host_api_count()):
                    api = pa2.get_host_api_info_by_index(api_idx)
                    if "wasapi" in api["name"].lower():
                        wasapi_idx = api_idx
                        break
                if wasapi_idx is not None:
                    api = pa2.get_host_api_info_by_index(wasapi_idx)
                    default_out_idx = api.get("defaultOutputDevice", -1)
                    if default_out_idx >= 0:
                        default_out = pa2.get_device_info_by_index(default_out_idx)
                        default_name = default_out["name"]
                        for d in devices:
                            if default_name in d["name"]:
                                d["isDefault"] = True
                                break
            finally:
                pa2.terminate()
        except Exception:
            pass

    except ImportError:
        print("[list_devices] pyaudiowpatch not installed", file=sys.stderr)
    except Exception as exc:
        print(f"[list_devices] loopback error: {exc}", file=sys.stderr)
    return devices


def main() -> None:
    result = {
        "loopback": _list_loopback_devices(),
        "input": _list_input_devices(),
    }
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
