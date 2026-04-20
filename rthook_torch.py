"""PyInstaller runtime hook: 在 torch 加载前设置 DLL 搜索路径。"""
import os
import sys

if sys.platform == 'win32' and getattr(sys, 'frozen', False):
    base = sys._MEIPASS

    torch_lib = os.path.join(base, 'torch', 'lib')
    dll_dirs = [d for d in (base, torch_lib) if os.path.isdir(d)]

    # 注册 DLL 搜索目录
    for d in dll_dirs:
        try:
            os.add_dll_directory(d)
        except OSError:
            pass

    # 同时更新 PATH（回退搜索路径）
    os.environ['PATH'] = os.pathsep.join(dll_dirs) + os.pathsep + os.environ.get('PATH', '')
