# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — 将 Python 后端打包为独立可执行文件。

生成目录结构（onedir 模式）：
  dist/imok-backend/
    ├── imok-backend.exe
    ├── _internal/          (Python runtime + 依赖)
    └── ...

使用：
  cd <project_root>
  .venv\Scripts\pyinstaller build_backend.spec --noconfirm
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# 项目根目录
PROJECT_ROOT = Path(SPECPATH)

# ── 隐式导入 ──────────────────────────────────────────────────
# PyInstaller 静态分析无法发现的动态 import
hidden_imports = [
    # backend 子模块
    *collect_submodules('backend'),
    # numpy 2.x: _core 是核心模块，PyInstaller hook 可能漏收
    *collect_submodules('numpy._core'),
    # faster-whisper / ctranslate2
    'faster_whisper',
    'ctranslate2',
    # speechbrain 说话人识别
    'speechbrain',
    'speechbrain.inference',
    'speechbrain.inference.speaker',
    # torch 相关
    'torch',
    'torchaudio',
    # 音频
    'sounddevice',
    'pyaudiowpatch',
    'scipy.signal',
    # pydantic
    'pydantic',
    'pydantic_settings',
    # httpx (LLM 客户端)
    'httpx',
    # comtypes (WASAPI)
    'comtypes',
    'comtypes.stream',
    # 标准库补充
    'asyncio',
    'json',
    'logging',
    'threading',
]

# ── 数据文件 ──────────────────────────────────────────────────
datas = [
    # backend 源码（某些模块可能动态加载）
    (str(PROJECT_ROOT / 'backend'), 'backend'),
    # detect_gpu 被 backend.config 动态导入
    (str(PROJECT_ROOT / 'scripts' / 'detect_gpu.py'), '.'),
]

# ── 二进制文件 ────────────────────────────────────────────────
binaries = []

# PyInstaller 有时漏收 torch/lib 下的关键 DLL（c10、cublas 等）
_torch_lib = PROJECT_ROOT / '.venv' / 'Lib' / 'site-packages' / 'torch' / 'lib'
for _dll_name in ('c10.dll', 'cublas64_12.dll', 'caffe2_nvrtc.dll'):
    _dll_path = _torch_lib / _dll_name
    if _dll_path.exists():
        binaries.append((str(_dll_path), 'torch/lib'))

# 尝试收集 ctranslate2 的 DLL
try:
    ct2_datas = collect_data_files('ctranslate2')
    datas.extend(ct2_datas)
except Exception:
    pass

# 收集 speechbrain 数据
try:
    sb_datas = collect_data_files('speechbrain')
    datas.extend(sb_datas)
except Exception:
    pass

# 收集 faster_whisper 数据
try:
    fw_datas = collect_data_files('faster_whisper')
    datas.extend(fw_datas)
except Exception:
    pass

# 收集 numpy 数据（numpy 2.x 的 _core 二进制文件）
try:
    np_datas = collect_data_files('numpy._core')
    datas.extend(np_datas)
except Exception:
    pass

a = Analysis(
    [str(PROJECT_ROOT / 'backend_entry.py')],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(PROJECT_ROOT / 'rthook_torch.py')],
    excludes=[
        'tkinter', 'matplotlib', 'PIL', 'IPython', 'notebook',
        'pytest', '_pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='imok-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 需要 stdin/stdout 通信
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='imok-backend',
)

# ── 后处理 ────────────────────────────────────────────────────
import shutil as _shutil

_dist_internal = Path(SPECPATH) / 'dist' / 'imok-backend' / '_internal'
_torch_lib = _dist_internal / 'torch' / 'lib'

# 1. 修复 vcruntime 版本冲突
#    PyInstaller 的 vcruntime 14.36 < torch CUDA 要求的 14.44 → WinError 1114
_sys32 = Path(r'C:\Windows\System32')
for _vcr in ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll'):
    _src = _sys32 / _vcr
    _dst = _dist_internal / _vcr
    if _src.exists() and _dst.exists():
        _shutil.copy2(str(_src), str(_dst))
        print(f'  [post-build] Replaced {_vcr} with system version')

# 2. 移除推理不需要的 CUDA DLL（节省 ~410 MB）
#    这些 DLL 不在任何必需 DLL 的 PE 导入表中，已通过运行时测试验证。
_excludable_dlls = [
    'nvrtc64_120_0.alt.dll',    # 83 MB - CUDA 运行时编译（备选）
    'nvrtc64_120_0.dll',        # 83 MB - CUDA 运行时编译
    'nvrtc-builtins64_128.dll', #  6 MB - NVRTC 内置函数
    'cusolverMg64_11.dll',      # 150 MB - 多 GPU 求解器
    'curand64_10.dll',          # 69 MB - 随机数生成（推理模式下不需要）
    'nvperf_host.dll',          # 21 MB - 性能计数器
    'cufftw64_11.dll',          #  0.2 MB - FFTW 兼容层
    'caffe2_nvrtc.dll',         # Caffe2 NVRTC
    'libiompstubs5md.dll',      # OpenMP 存根
    'nvToolsExt64_1.dll',       # NVIDIA 工具扩展
]
_removed_mb = 0
for _dll in _excludable_dlls:
    _p = _torch_lib / _dll
    if _p.exists():
        _sz = _p.stat().st_size / 1024 / 1024
        _p.unlink()
        _removed_mb += _sz
if _removed_mb > 0:
    print(f'  [post-build] Removed {len(_excludable_dlls)} unneeded CUDA DLLs ({_removed_mb:.0f} MB saved)')

# 3. 移除推理不需要的 torch 子目录（节省 ~30 MB）
for _subdir in ('_inductor', 'testing', 'distributed', '_dynamo', 'bin',
                'ao', 'onnx', '_export', '_functorch', 'fx',
                'profiler', 'package', 'xpu', 'mps', 'mtia'):
    _d = _dist_internal / 'torch' / _subdir
    if _d.exists():
        _shutil.rmtree(str(_d), ignore_errors=True)
print('  [post-build] Removed unneeded torch subdirectories')
