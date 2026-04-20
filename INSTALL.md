# Imok Meeting Assistant — 安装说明

本文档适用于 **轻量版（Lite）** 分发包。轻量版不包含 Python 运行环境，
需要用户自行安装 Python 和相关依赖。

> 如果你使用的是**完整版**（文件名不含 `lite`），Python 已内置，无需额外安装，
> 直接运行 `Imok Meeting Assistant.exe` 即可。

---

## 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10/11 (64-bit) |
| Python | **3.12.x**（推荐 3.12.8+） |
| GPU（推荐） | NVIDIA 显卡 + CUDA 12.x 驱动 |
| 内存 | 8 GB+（推荐 16 GB） |
| 磁盘 | 约 5 GB（含模型和依赖） |

---

## 一、安装 Python 3.12

1. 从 [python.org](https://www.python.org/downloads/) 下载 Python 3.12.x 安装包
2. 安装时 **勾选 "Add Python to PATH"**
3. 验证安装：

```powershell
python --version
# 应输出: Python 3.12.x
```

---

## 二、安装 Python 依赖

打开 PowerShell，进入应用的 `resources` 目录（与 `Imok Meeting Assistant.exe` 同级的 `resources` 文件夹）：

```powershell
cd "你的安装路径\resources"

# 创建虚拟环境（推荐）
python -m venv .venv
.venv\Scripts\Activate.ps1

# 安装依赖
pip install -r requirements.txt
```

### GPU 加速（推荐）

如果你有 NVIDIA 显卡，安装 CUDA 版本的 PyTorch 以获得更快的语音识别速度：

```powershell
# 先安装基础依赖
pip install -r requirements.txt

# 然后用 CUDA 12.1 版本替换 torch（根据你的 CUDA 版本选择）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```

> 查看你的 CUDA 版本：在命令行运行 `nvidia-smi`，右上角显示 CUDA Version。

### 仅 CPU 模式

如果没有 NVIDIA 显卡，直接安装即可（会使用 CPU，速度较慢但可用）：

```powershell
pip install -r requirements.txt
```

---

## 三、配置

### 3.1 环境变量

将 `resources\.env.example` 复制为 `resources\.env`，并填入你的配置：

```powershell
cd resources
copy .env.example .env
# 用编辑器打开 .env，填入 API_TOKEN 等信息
```

### 3.2 LLM 配置（可选，用于会议总结功能）

将 `resources\config\llm_providers.yaml.example` 复制为 `resources\config\llm_providers.yaml`：

```powershell
copy config\llm_providers.yaml.example config\llm_providers.yaml
# 编辑 llm_providers.yaml，填入你的 LLM API 地址和密钥
```

---

## 四、运行

直接双击 `Imok Meeting Assistant.exe` 启动应用。

应用会自动查找系统 PATH 中的 `python` 命令来启动后端服务。

> 如果使用了虚拟环境，请确保在启动应用前激活虚拟环境，
> 或将虚拟环境中的 Python 路径添加到系统 PATH。

---

## 常见问题

### Q: 提示找不到 Python 或模块

确认 Python 已添加到系统 PATH：

```powershell
python --version          # 应输出版本号
python -c "import torch"  # 不应报错
```

### Q: 语音识别很慢

- 检查是否安装了 CUDA 版本的 PyTorch：
  ```powershell
  python -c "import torch; print(torch.cuda.is_available())"
  # 应输出: True
  ```
- 如果输出 `False`，参考上面"GPU 加速"部分重新安装

### Q: 启动报错 "No module named xxx"

重新安装依赖：

```powershell
cd "安装路径\resources"
pip install -r requirements.txt
```
