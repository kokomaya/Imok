"""PyInstaller runtime hook: 在 torch 加载前设置 DLL 搜索路径。"""
import os
import sys
import ctypes

if sys.platform == 'win32' and getattr(sys, 'frozen', False):
    base = sys._MEIPASS

    # 收集所有包含 DLL 的目录
    dll_dirs = [base]

    torch_lib = os.path.join(base, 'torch', 'lib')
    if os.path.isdir(torch_lib):
        dll_dirs.append(torch_lib)

    # 添加所有目录到 DLL 搜索路径
    for d in dll_dirs:
        try:
            os.add_dll_directory(d)
        except OSError:
            pass

    os.environ['PATH'] = os.pathsep.join(dll_dirs) + os.pathsep + os.environ.get('PATH', '')

    # 预加载 MSVC runtime
    for rt in ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll'):
        try:
            ctypes.CDLL(os.path.join(base, rt))
        except OSError:
            pass

    # 预加载 c10.dll 及其直接依赖
    for dll_name in ('c10.dll', 'c10_cuda.dll', 'torch_cpu.dll', 'torch_cuda.dll'):
        p = os.path.join(torch_lib, dll_name)
        if os.path.isfile(p):
            try:
                ctypes.CDLL(p)
            except OSError:
                pass
