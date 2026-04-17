"""GPU 检测工具 — 单一职责：检测 CUDA 可用性并推荐计算配置。

独立于 config.py，可作为命令行脚本运行，也可被 config 模块导入调用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GPUInfo:
    """GPU 检测结果。"""

    cuda_available: bool
    device_name: str
    vram_mb: int
    cuda_version: str
    recommended_model_size: str  # "large-v3" | "medium" | "small"
    recommended_compute_type: str  # "float16" | "int8" | "int8_float16"
    recommended_device: str  # "cuda" | "cpu"


def detect_gpu() -> GPUInfo:
    """检测系统 GPU 并返回推荐配置。"""
    try:
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA not available")

        device_name = torch.cuda.get_device_name(0)
        props = torch.cuda.get_device_properties(0)
        total_bytes = getattr(props, "total_memory", None) or getattr(props, "total_mem", 0)
        vram_mb = total_bytes // (1024 * 1024)
        cuda_version = torch.version.cuda or "unknown"

        # 根据显存大小推荐模型
        if vram_mb >= 8000:
            model_size = "large-v3"
            compute_type = "float16"
        elif vram_mb >= 4000:
            model_size = "medium"
            compute_type = "int8_float16"
        else:
            model_size = "small"
            compute_type = "int8"

        logger.info(
            "GPU detected: %s (%d MB VRAM) → model=%s, compute=%s",
            device_name,
            vram_mb,
            model_size,
            compute_type,
        )

        return GPUInfo(
            cuda_available=True,
            device_name=device_name,
            vram_mb=vram_mb,
            cuda_version=cuda_version,
            recommended_model_size=model_size,
            recommended_compute_type=compute_type,
            recommended_device="cuda",
        )

    except Exception as exc:
        logger.info("No CUDA GPU detected (%s), falling back to CPU.", exc)
        return GPUInfo(
            cuda_available=False,
            device_name="cpu",
            vram_mb=0,
            cuda_version="N/A",
            recommended_model_size="medium",
            recommended_compute_type="int8",
            recommended_device="cpu",
        )


def print_report(info: GPUInfo) -> None:
    """打印 GPU 检测报告到标准输出。"""
    print("=" * 50)
    print("  GPU Detection Report")
    print("=" * 50)
    print(f"  CUDA Available   : {info.cuda_available}")
    print(f"  Device           : {info.device_name}")
    print(f"  VRAM             : {info.vram_mb} MB")
    print(f"  CUDA Version     : {info.cuda_version}")
    print("-" * 50)
    print(f"  Recommended Model      : {info.recommended_model_size}")
    print(f"  Recommended Compute    : {info.recommended_compute_type}")
    print(f"  Recommended Device     : {info.recommended_device}")
    print("=" * 50)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gpu_info = detect_gpu()
    print_report(gpu_info)
