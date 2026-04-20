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

# ── 修复 vcruntime 版本冲突 ──────────────────────────────────
# PyInstaller 打包的 vcruntime 来自 Python 安装（14.36），但 torch CUDA 12.8
# 编译所用 MSVC 版本更新（14.44）。c10.dll 的 DllMain 在加载旧版 vcruntime 时
# 返回 FALSE（WinError 1114）。用系统最新版覆盖即可解决。
import shutil as _shutil

_dist_internal = Path(SPECPATH) / 'dist' / 'imok-backend' / '_internal'
_vcr_names = ['vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll']
_sys32 = Path(r'C:\Windows\System32')

for _vcr in _vcr_names:
    _src = _sys32 / _vcr
    _dst = _dist_internal / _vcr
    if _src.exists() and _dst.exists():
        _shutil.copy2(str(_src), str(_dst))
        print(f'  [post-build] Replaced {_vcr} with system version')
