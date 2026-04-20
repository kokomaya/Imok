"""PyInstaller 入口点 — 等同于 python -m backend.main。"""
import sys
import os

# 确保项目根目录在 sys.path 中
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后，_MEIPASS 是临时解压目录
    base_dir = sys._MEIPASS

    # Windows: 把 _internal 本身加入 DLL 搜索路径（MSVC runtime 等在此处），
    # 以及 torch\lib（c10.dll, torch_cpu.dll 等）、nvidia 子目录。
    # 必须在 import torch 之前完成，否则 LoadLibraryExW 会失败。
    dll_dirs = [base_dir]

    torch_lib = os.path.join(base_dir, 'torch', 'lib')
    if os.path.isdir(torch_lib):
        dll_dirs.append(torch_lib)

    # nvidia runtime DLLs (cublas, cudnn etc.)
    nvidia_dir = os.path.join(base_dir, 'nvidia')
    if os.path.isdir(nvidia_dir):
        for root, dirs, files in os.walk(nvidia_dir):
            if any(f.endswith('.dll') for f in files):
                dll_dirs.append(root)

    for d in dll_dirs:
        os.add_dll_directory(d)
    os.environ['PATH'] = os.pathsep.join(dll_dirs) + os.pathsep + os.environ.get('PATH', '')
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

if base_dir not in sys.path:
    sys.path.insert(0, base_dir)


def _run_module(module_name):
    """运行指定的 backend 子模块（等同于 python -m <module>）。"""
    import importlib
    import runpy
    runpy.run_module(module_name, run_name='__main__', alter_sys=True)


if __name__ == '__main__':
    # 支持 --run <module> 子命令，用于执行 backend 子模块
    # 例: imok-backend.exe --run backend.audio.list_devices
    if len(sys.argv) >= 3 and sys.argv[1] == '--run':
        module_name = sys.argv[2]
        # 将 --run <module> 之后的参数传递给子模块
        sys.argv = sys.argv[2:]
        _run_module(module_name)
    else:
        from backend.main import main
        main()
