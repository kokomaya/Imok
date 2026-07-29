"""Windows 麦克风端点静音检测与解除。

调用 Windows Core Audio COM API (pycaw) 检查/解除指定采集端点的静音状态。
用于解决 sounddevice 无法采集被系统静音的麦克风的问题。

注：通信软件（Teams、Zoom）可绕过系统端点静音，但 sounddevice/PyAudio 不行。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_PYCAW_AVAILABLE = False
try:
    import comtypes
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import (
        AudioUtilities,
        IAudioEndpointVolume,
        EDataFlow,
        DEVICE_STATE,
    )
    _PYCAW_AVAILABLE = True
except ImportError:
    pass


def ensure_mic_unmuted(device_name: Optional[str] = None) -> bool:
    """检查并解除麦克风端点静音。

    遍历所有 Active 的采集端点，如果检测到静音则自动解除。

    Args:
        device_name: 可选的设备名称关键字过滤，None 表示所有采集端点。

    Returns:
        True 如果有端点被解除静音，False 如果没有或不可用。
    """
    if not _PYCAW_AVAILABLE:
        logger.debug("pycaw not available, skipping mic mute check")
        return False

    # 显式初始化 COM（MTA 模式），确保当前线程 COM 就绪，
    # 并在 finally 中配对 CoUninitialize 释放。
    comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    try:
        return _do_unmute(device_name)
    finally:
        comtypes.CoUninitialize()


def _do_unmute(device_name: Optional[str]) -> bool:
    unmuted_any = False
    try:
        enumerator = AudioUtilities.GetDeviceEnumerator()
        collection = enumerator.EnumAudioEndpoints(
            EDataFlow.eCapture.value, DEVICE_STATE.ACTIVE.value
        )
        for i in range(collection.GetCount()):
            dev = collection.Item(i)
            vol = None
            try:
                # 用 QueryInterface 代替 ctypes.cast，
                # 确保 AddRef 正确计数，避免 double-free
                iface = dev.Activate(
                    IAudioEndpointVolume._iid_, CLSCTX_ALL, None
                )
                vol = iface.QueryInterface(IAudioEndpointVolume)
                if vol.GetMute():
                    vol.SetMute(0, None)
                    logger.info("Unmuted capture endpoint %d", i)
                    unmuted_any = True
            except Exception:
                logger.debug("Could not check/unmute endpoint %d", i, exc_info=True)
            finally:
                # 在同一线程上显式按序释放，避免 GC 跨线程释放
                del vol
                del iface
                del dev
        del collection
        del enumerator
    except Exception:
        logger.debug("Failed to enumerate capture endpoints", exc_info=True)

    return unmuted_any
