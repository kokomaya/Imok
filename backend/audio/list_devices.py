"""列举音频设备 — 输出 JSON 到 stdout，供 Electron 主进程调用。

用法：python -m backend.audio.list_devices
输出：{ "loopback": [...], "input": [...] }
"""

from __future__ import annotations

import json
import sys


def _list_input_devices() -> list[dict]:
    """列出输入设备（sounddevice）。

    只展示 Windows WASAPI 设备（最佳质量/最低延迟），
    过滤虚拟 mapper 和 MME/WDM-KS 重复条目。
    若无 WASAPI 设备则回退到 DirectSound。
    """
    PREFERRED_API = "Windows WASAPI"
    FALLBACK_API = "Windows DirectSound"
    # 虚拟 mapper 设备名关键词（非真实物理设备）
    VIRTUAL_KEYWORDS = ("Sound Mapper", "Primary Sound")

    devices = []
    try:
        import sounddevice as sd

        host_apis = sd.query_hostapis()
        all_devs = []
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] <= 0:
                continue
            api_name = (
                host_apis[dev["hostapi"]]["name"]
                if dev["hostapi"] < len(host_apis)
                else "Unknown"
            )
            name = dev["name"]
            # 跳过虚拟 mapper 设备
            if any(kw in name for kw in VIRTUAL_KEYWORDS):
                continue
            all_devs.append({
                "index": idx,
                "name": name,
                "hostApi": api_name,
                "maxInputChannels": dev["max_input_channels"],
                "sampleRate": dev["default_samplerate"],
                "isLoopback": False,
                "isDefault": idx == sd.default.device[0],
            })

        # 优先取 WASAPI 设备；没有的话回退到 DirectSound；都没有则全部返回
        wasapi = [d for d in all_devs if PREFERRED_API in d["hostApi"]]
        if wasapi:
            devices = wasapi
        else:
            ds = [d for d in all_devs if FALLBACK_API in d["hostApi"]]
            devices = ds if ds else all_devs
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
