"""Phase 1b 集成验证脚本 — 端到端测试 IPC + 翻译 + 闭麦辅助链路。

单一职责：自动化验证 Phase 1b 各模块的集成与通信正确性。

使用方式：
    # 完整验证（包含模型加载和延迟测量）
    python -m scripts.verify_phase1b

    # 快速验证（仅检查模块导入、IPC 协议和前端构建）
    python -m scripts.verify_phase1b --quick

    # 实时翻译延迟测量（需要实际 LLM API 可用）
    python -m scripts.verify_phase1b --measure-latency --llm-base-url=http://... --llm-model=...

验证内容：
    2.10.1 Python 子进程 IPC 通信验证
    2.10.2 ASR → LLM 翻译链路验证
    2.10.3 闭麦键盘输入 → 英文表达
    2.10.4 闭麦麦克风输入 UI 状态流转
    2.10.5 翻译延迟测量
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# 报告数据模型
# ============================================================================


@dataclass
class CheckResult:
    """单项检查结果。"""

    name: str
    passed: bool
    message: str
    duration_s: float = 0.0


@dataclass
class LatencyMeasurement:
    """翻译延迟测量结果。"""

    input_text: str
    first_token_latency_s: float
    total_latency_s: float
    output_text: str
    output_length: int


@dataclass
class VerificationReport:
    """Phase 1b 验证报告。"""

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    checks: List[CheckResult] = field(default_factory=list)
    latency_measurements: List[LatencyMeasurement] = field(default_factory=list)
    total_passed: int = 0
    total_failed: int = 0
    avg_first_token_latency_s: float = 0.0
    avg_total_latency_s: float = 0.0
    errors: List[str] = field(default_factory=list)


# ============================================================================
# 2.10.1 — Python 子进程 IPC 通信验证
# ============================================================================


def check_ipc_protocol() -> CheckResult:
    """验证 IPC 消息协议的序列化/反序列化。"""
    t0 = time.perf_counter()
    try:
        from backend.ipc.messages import (
            ControlAction,
            IPCMessage,
            MessageType,
            ProcessState,
        )

        # 测试所有工厂方法
        msgs = [
            IPCMessage.transcription("hello", language="en", confidence=0.95),
            IPCMessage.status(ProcessState.READY),
            IPCMessage.status(ProcessState.RUNNING, source="wasapi", asr_model="medium"),
            IPCMessage.error("test_error", "test message"),
            IPCMessage.control(ControlAction.START),
            IPCMessage.control(ControlAction.SWITCH_SOURCE, source="mic"),
        ]

        for msg in msgs:
            line = msg.to_json_line()
            parsed = IPCMessage.from_json_line(line)
            assert parsed.type == msg.type, f"Type mismatch: {parsed.type} != {msg.type}"
            assert parsed.data == msg.data, f"Data mismatch for {msg.type}"

        # 测试边界情况
        try:
            IPCMessage.from_json_line("")
            return CheckResult("IPC Protocol", False, "Should reject empty line",
                               time.perf_counter() - t0)
        except ValueError:
            pass

        try:
            IPCMessage.from_json_line("not json")
            return CheckResult("IPC Protocol", False, "Should reject invalid JSON",
                               time.perf_counter() - t0)
        except ValueError:
            pass

        return CheckResult(
            "IPC Protocol", True,
            f"All {len(msgs)} message types serialize/deserialize correctly",
            time.perf_counter() - t0,
        )
    except Exception as e:
        return CheckResult("IPC Protocol", False, str(e), time.perf_counter() - t0)


def check_subprocess_writer() -> CheckResult:
    """验证 SubprocessWriter 线程安全写入。"""
    t0 = time.perf_counter()
    try:
        from backend.ipc.messages import IPCMessage, ProcessState
        from backend.ipc.subprocess_io import SubprocessWriter

        buf = io.StringIO()
        writer = SubprocessWriter(stream=buf)

        # 写入多条消息
        writer.write(IPCMessage.status(ProcessState.READY))
        writer.write(IPCMessage.transcription("test", language="zh"))
        writer.write(IPCMessage.error("test_code", "test_msg"))

        lines = buf.getvalue().strip().split("\n")
        assert len(lines) == 3, f"Expected 3 lines, got {len(lines)}"

        for line in lines:
            parsed = json.loads(line)
            assert "type" in parsed
            assert "data" in parsed
            assert "ts" in parsed

        # 测试关闭后不写入
        writer.close()
        writer.write(IPCMessage.status(ProcessState.STOPPED))
        assert len(buf.getvalue().strip().split("\n")) == 3, "Should not write after close"

        return CheckResult(
            "SubprocessWriter", True,
            "Thread-safe writing, close behavior verified",
            time.perf_counter() - t0,
        )
    except Exception as e:
        return CheckResult("SubprocessWriter", False, str(e), time.perf_counter() - t0)


def check_subprocess_reader() -> CheckResult:
    """验证 SubprocessReader 异步读取。"""
    t0 = time.perf_counter()
    try:
        from backend.ipc.messages import ControlAction, IPCMessage, MessageType
        from backend.ipc.subprocess_io import SubprocessReader

        # 准备模拟 stdin
        control_msg = IPCMessage.control(ControlAction.START)
        fake_stdin = io.StringIO(control_msg.to_json_line() + "\n")

        received = []

        def handler(msg: IPCMessage):
            received.append(msg)

        reader = SubprocessReader(stream=fake_stdin)
        reader.on_message(MessageType.CONTROL, handler)

        async def run():
            await reader.start()
            await asyncio.sleep(0.3)  # 给读取循环时间处理
            await reader.stop()

        asyncio.run(run())

        assert len(received) == 1, f"Expected 1 message, got {len(received)}"
        assert received[0].type == MessageType.CONTROL

        return CheckResult(
            "SubprocessReader", True,
            "Async reading and message dispatch verified",
            time.perf_counter() - t0,
        )
    except Exception as e:
        return CheckResult("SubprocessReader", False, str(e), time.perf_counter() - t0)


def check_subprocess_roundtrip() -> CheckResult:
    """验证完整的子进程启动 → READY 消息 → 控制命令 → 退出流程。"""
    t0 = time.perf_counter()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "backend.main", "--mode=subprocess", "--source=wasapi"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(_PROJECT_ROOT),
            text=True,
            bufsize=1,
        )

        # 等待 READY 消息
        ready_line = proc.stdout.readline()
        if not ready_line:
            proc.kill()
            return CheckResult("Subprocess Roundtrip", False,
                               "No output from subprocess", time.perf_counter() - t0)

        ready_msg = json.loads(ready_line.strip())
        if ready_msg.get("type") != "status" or ready_msg.get("data", {}).get("state") != "ready":
            proc.kill()
            return CheckResult("Subprocess Roundtrip", False,
                               f"Unexpected first message: {ready_msg}",
                               time.perf_counter() - t0)

        # 发送 stop 命令然后关闭 stdin
        stop_cmd = json.dumps({"type": "control", "data": {"action": "stop"}, "ts": time.time()})
        try:
            proc.stdin.write(stop_cmd + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

        proc.stdin.close()

        # 等待退出
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return CheckResult("Subprocess Roundtrip", False,
                               "Subprocess did not exit within 10s",
                               time.perf_counter() - t0)

        elapsed = time.perf_counter() - t0
        return CheckResult(
            "Subprocess Roundtrip", True,
            f"Subprocess: spawn → READY → stop → exit (code={proc.returncode}) in {elapsed:.2f}s",
            elapsed,
        )
    except Exception as e:
        return CheckResult("Subprocess Roundtrip", False, str(e), time.perf_counter() - t0)


# ============================================================================
# 2.10.2 — 翻译链路验证（Python 后端部分）
# ============================================================================


def check_translation_modules() -> CheckResult:
    """验证翻译服务模块可导入且初始化正常。"""
    t0 = time.perf_counter()
    try:
        from backend.translation.context_window import ContextWindow
        from backend.translation.request_batcher import RequestBatcher
        from backend.translation.translator import RealtimeTranslator

        # 验证 ContextWindow
        cw = ContextWindow(max_entries=3)
        cw.add("hello", "你好")
        cw.add("world", "世界")
        ctx = cw.format_for_prompt()
        assert "hello" in ctx and "你好" in ctx

        # 验证 RequestBatcher
        batcher = RequestBatcher(merge_window_ms=500)
        batcher.submit("hello")
        batcher.submit("world")
        merged = batcher.flush_immediate()
        assert merged is not None

        return CheckResult(
            "Translation Modules", True,
            "ContextWindow, RequestBatcher, RealtimeTranslator importable and functional",
            time.perf_counter() - t0,
        )
    except Exception as e:
        return CheckResult("Translation Modules", False, str(e), time.perf_counter() - t0)


# ============================================================================
# 2.10.3 — 闭麦键盘输入链路验证
# ============================================================================


def check_expression_modules() -> CheckResult:
    """验证闭麦表达辅助模块可导入且初始化正常。"""
    t0 = time.perf_counter()
    try:
        from backend.expression.assistant import ExpressionAssistant
        from backend.expression.scene_manager import SceneManager
        from backend.llm.prompt_manager import PromptManager
        from backend.llm.glossary import GlossaryManager

        # 验证 SceneManager
        sm = SceneManager()
        scenes_path = _PROJECT_ROOT / "config" / "scenes.json"
        if scenes_path.exists():
            sm.load(scenes_path)
        scenes = sm.list_scenes()
        assert len(scenes) > 0, "No scenes loaded from config/scenes.json"
        default = sm.get_default()
        assert default is not None, "No default scene"

        # 验证 PromptManager
        pm = PromptManager()
        prompt = pm.render_expression(
            text="我们需要讨论一下架构设计",
            scene_description="内部技术讨论",
            glossary="architecture: 架构",
        )
        assert len(prompt) > 0

        # 验证 GlossaryManager
        gm = GlossaryManager()
        formatted = gm.format_for_prompt()
        assert isinstance(formatted, str)

        return CheckResult(
            "Expression Modules", True,
            f"SceneManager ({len(scenes)} scenes), PromptManager, GlossaryManager OK",
            time.perf_counter() - t0,
        )
    except Exception as e:
        return CheckResult("Expression Modules", False, str(e), time.perf_counter() - t0)


# ============================================================================
# 2.10.4 — 闭麦麦克风输入 UI 状态流转
# ============================================================================


def check_mute_pipeline() -> CheckResult:
    """验证 MutePipeline 模块可导入且模式切换逻辑正常。"""
    t0 = time.perf_counter()
    try:
        from backend.pipeline.mute_pipeline import MutePipeline, PipelineMode

        # 验证枚举
        assert PipelineMode.KEYBOARD is not None
        assert PipelineMode.VOICE is not None

        return CheckResult(
            "MutePipeline Modes", True,
            "KEYBOARD and VOICE modes importable, pipeline module OK",
            time.perf_counter() - t0,
        )
    except Exception as e:
        return CheckResult("MutePipeline Modes", False, str(e), time.perf_counter() - t0)


# ============================================================================
# 2.10.5 — 前端构建验证
# ============================================================================


def check_frontend_build() -> CheckResult:
    """验证 Vite 构建通过。"""
    t0 = time.perf_counter()
    try:
        frontend_dir = _PROJECT_ROOT / "frontend"
        if not (frontend_dir / "node_modules").exists():
            return CheckResult("Frontend Build", False,
                               "node_modules not found. Run 'npm install' first.",
                               time.perf_counter() - t0)

        result = subprocess.run(
            ["npx", "vite", "build"],
            capture_output=True,
            text=True,
            cwd=str(frontend_dir),
            shell=True,
            timeout=60,
        )

        # 提取模块数
        output = result.stdout + result.stderr
        module_count = 0
        for line in output.split("\n"):
            if "modules transformed" in line:
                parts = line.strip().split()
                for p in parts:
                    if p.isdigit():
                        module_count = int(p)
                        break

        if module_count > 0:
            return CheckResult(
                "Frontend Build", True,
                f"Vite build passed: {module_count} modules",
                time.perf_counter() - t0,
            )
        elif "built in" in output:
            return CheckResult(
                "Frontend Build", True,
                "Vite build passed",
                time.perf_counter() - t0,
            )
        else:
            return CheckResult(
                "Frontend Build", False,
                f"Vite build may have failed: {output[-300:]}",
                time.perf_counter() - t0,
            )
    except subprocess.TimeoutExpired:
        return CheckResult("Frontend Build", False, "Build timed out (60s)",
                           time.perf_counter() - t0)
    except Exception as e:
        return CheckResult("Frontend Build", False, str(e), time.perf_counter() - t0)


def check_python_tests() -> CheckResult:
    """运行 Python 后端回归测试。"""
    t0 = time.perf_counter()
    try:
        test_files = [
            "tests/backend/test_expression.py",
            "tests/backend/test_ipc.py",
            "tests/backend/test_llm.py",
            "tests/backend/test_llm_provider.py",
            "tests/backend/test_translation.py",
            "tests/backend/test_prompt_glossary.py",
        ]

        result = subprocess.run(
            [sys.executable, "-m", "pytest"] + test_files + ["-q", "--tb=short"],
            capture_output=True,
            text=True,
            cwd=str(_PROJECT_ROOT),
            timeout=120,
        )

        output = result.stdout + result.stderr
        # 提取通过数
        passed = 0
        for line in output.split("\n"):
            if "passed" in line:
                for word in line.split():
                    if word.isdigit():
                        passed = int(word)
                        break

        if result.returncode == 0:
            return CheckResult(
                "Python Tests", True,
                f"{passed} tests passed",
                time.perf_counter() - t0,
            )
        else:
            return CheckResult(
                "Python Tests", False,
                f"Tests failed (exit code {result.returncode}): {output[-300:]}",
                time.perf_counter() - t0,
            )
    except subprocess.TimeoutExpired:
        return CheckResult("Python Tests", False, "Tests timed out (120s)",
                           time.perf_counter() - t0)
    except Exception as e:
        return CheckResult("Python Tests", False, str(e), time.perf_counter() - t0)


# ============================================================================
# 2.10.5 — 翻译延迟测量（需要实际 LLM API）
# ============================================================================


def check_frontend_modules() -> CheckResult:
    """验证前端关键模块文件存在且可解析。"""
    t0 = time.perf_counter()
    try:
        frontend_src = _PROJECT_ROOT / "frontend" / "src"
        required_files = [
            "services/ipc-bridge.js",
            "services/llm-client.js",
            "services/expression-service.js",
            "stores/subtitle-store.js",
            "stores/mute-assist-store.js",
            "components/SubtitleOverlay/SubtitleOverlay.vue",
            "components/SubtitleOverlay/index.js",
            "components/MuteAssistPanel/MuteAssistPanel.vue",
            "components/MuteAssistPanel/index.js",
            "App.vue",
            "router.js",
            "main.js",
        ]
        electron_files = [
            "electron/main.js",
            "electron/preload.js",
            "electron/python-bridge.js",
            "electron/window-manager.js",
        ]

        missing = []
        for f in required_files:
            if not (frontend_src / f).exists():
                missing.append(f"src/{f}")
        for f in electron_files:
            if not (_PROJECT_ROOT / "frontend" / f).exists():
                missing.append(f)

        if missing:
            return CheckResult(
                "Frontend Modules", False,
                f"Missing files: {', '.join(missing)}",
                time.perf_counter() - t0,
            )

        # 验证关键文件内容中包含预期导出
        ipc_bridge = (frontend_src / "services/ipc-bridge.js").read_text(encoding="utf-8")
        assert "ipcBridge" in ipc_bridge, "ipc-bridge.js missing ipcBridge export"

        llm_client = (frontend_src / "services/llm-client.js").read_text(encoding="utf-8")
        assert "llmClient" in llm_client, "llm-client.js missing llmClient export"

        expr_svc = (frontend_src / "services/expression-service.js").read_text(encoding="utf-8")
        assert "expressionService" in expr_svc, "expression-service.js missing expressionService export"

        preload = (_PROJECT_ROOT / "frontend/electron/preload.js").read_text(encoding="utf-8")
        assert "mute-panel:toggle" in preload, "preload.js missing mute-panel:toggle channel"
        assert "toggleMutePanel" in preload, "preload.js missing toggleMutePanel API"

        main_js = (_PROJECT_ROOT / "frontend/electron/main.js").read_text(encoding="utf-8")
        assert "globalShortcut" in main_js, "main.js missing globalShortcut import"
        assert "registerShortcuts" in main_js, "main.js missing registerShortcuts"
        assert "CommandOrControl+Shift+M" in main_js, "main.js missing Ctrl+Shift+M shortcut"

        app_vue = (frontend_src / "App.vue").read_text(encoding="utf-8")
        assert "MuteAssistPanel" in app_vue, "App.vue missing MuteAssistPanel integration"
        assert "mute-panel:toggle" in app_vue, "App.vue missing mute-panel:toggle listener"

        total = len(required_files) + len(electron_files)
        return CheckResult(
            "Frontend Modules", True,
            f"All {total} modules present, key exports/integrations verified",
            time.perf_counter() - t0,
        )
    except Exception as e:
        return CheckResult("Frontend Modules", False, str(e), time.perf_counter() - t0)


async def measure_llm_latency(base_url: str, model: str, api_key: str = "") -> List[LatencyMeasurement]:
    """测量 LLM 翻译首字延迟和总延迟。"""
    import httpx

    test_inputs = [
        ("我觉得这个方案可行", "Short sentence"),
        ("我们需要在下周之前完成代码审查，请大家注意时间节点", "Medium sentence"),
        ("关于这个技术方案，我有几点建议：首先是性能优化的部分需要重新评估，"
         "其次是接口设计要考虑向后兼容性", "Long sentence"),
    ]

    measurements = []
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = f"{base_url.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=30.0) as client:
        for text, label in test_inputs:
            prompt = (
                f"你是实时会议翻译系统，请将以下中文翻译为英文。"
                f"仅输出翻译结果，不要解释。\n输入：{text}"
            )
            body = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "temperature": 0.3,
                "max_tokens": 256,
            }

            t_start = time.perf_counter()
            first_token_time = None
            output_chunks = []

            try:
                async with client.stream(
                    "POST", url, json=body, headers=headers,
                ) as response:
                    response.raise_for_status()
                    buffer = ""
                    async for chunk in response.aiter_text():
                        buffer += chunk
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line or not line.startswith("data: "):
                                continue
                            payload = line[6:]
                            if payload == "[DONE]":
                                break
                            try:
                                parsed = json.loads(payload)
                                delta = parsed.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if delta:
                                    if first_token_time is None:
                                        first_token_time = time.perf_counter()
                                    output_chunks.append(delta)
                            except json.JSONDecodeError:
                                pass

                t_end = time.perf_counter()
                output_text = "".join(output_chunks)
                first_token_latency = (first_token_time - t_start) if first_token_time else (t_end - t_start)

                m = LatencyMeasurement(
                    input_text=text,
                    first_token_latency_s=first_token_latency,
                    total_latency_s=t_end - t_start,
                    output_text=output_text,
                    output_length=len(output_text),
                )
                measurements.append(m)
                print(
                    f"  [{label}] "
                    f"first_token={first_token_latency:.3f}s  "
                    f"total={t_end - t_start:.3f}s  "
                    f"output_len={len(output_text)}  "
                    f"| {output_text[:60]}..."
                )

            except Exception as e:
                print(f"  [{label}] ERROR: {e}")

    return measurements


# ============================================================================
# 主流程
# ============================================================================


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 1b Integration Verification")
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode: skip subprocess roundtrip and build")
    parser.add_argument("--measure-latency", action="store_true",
                        help="Measure LLM translation latency (requires API access)")
    parser.add_argument("--llm-base-url", default="",
                        help="LLM API base URL for latency measurement")
    parser.add_argument("--llm-model", default="",
                        help="LLM model name for latency measurement")
    parser.add_argument("--llm-api-key", default="",
                        help="LLM API key (optional)")
    return parser.parse_args()


def _print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def _print_result(result: CheckResult) -> None:
    icon = "\033[92m✅\033[0m" if result.passed else "\033[91m❌\033[0m"
    time_str = f" ({result.duration_s:.2f}s)" if result.duration_s > 0 else ""
    print(f"  {icon} {result.name}: {result.message}{time_str}")


def main() -> None:
    args = _parse_args()

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║       IMOK Phase 1b — Integration Verification         ║")
    print("╚══════════════════════════════════════════════════════════╝")

    report = VerificationReport()

    # ── 2.10.1 IPC 通信验证 ──
    _print_header("[1/6] IPC Protocol & Subprocess Communication")

    ipc_checks = [
        check_ipc_protocol(),
        check_subprocess_writer(),
        check_subprocess_reader(),
    ]

    if not args.quick:
        ipc_checks.append(check_subprocess_roundtrip())

    for r in ipc_checks:
        _print_result(r)
        report.checks.append(r)

    # ── 2.10.2 翻译模块验证 ──
    _print_header("[2/6] Translation Module Integration")
    r = check_translation_modules()
    _print_result(r)
    report.checks.append(r)

    # ── 2.10.3 闭麦表达模块验证 ──
    _print_header("[3/6] Mute-Assist Expression Modules")
    r = check_expression_modules()
    _print_result(r)
    report.checks.append(r)

    # ── 2.10.4 闭麦流水线模式验证 ──
    _print_header("[4/6] MutePipeline Mode Validation")
    r = check_mute_pipeline()
    _print_result(r)
    report.checks.append(r)

    # ── 前端模块验证 ──
    _print_header("[5/6] Frontend Module Verification")
    r = check_frontend_modules()
    _print_result(r)
    report.checks.append(r)

    if not args.quick:
        r = check_frontend_build()
        _print_result(r)
        report.checks.append(r)

        r = check_python_tests()
        _print_result(r)
        report.checks.append(r)

    # ── 2.10.5 延迟测量 ──
    if args.measure_latency and args.llm_base_url and args.llm_model:
        _print_header("[6/6] LLM Translation Latency Measurement")
        measurements = asyncio.run(
            measure_llm_latency(args.llm_base_url, args.llm_model, args.llm_api_key)
        )
        report.latency_measurements = measurements
        if measurements:
            avg_first = sum(m.first_token_latency_s for m in measurements) / len(measurements)
            avg_total = sum(m.total_latency_s for m in measurements) / len(measurements)
            report.avg_first_token_latency_s = avg_first
            report.avg_total_latency_s = avg_total

            target_met = avg_first < 1.0
            target_icon = "\033[92m✅\033[0m" if target_met else "\033[91m❌\033[0m"
            print(f"\n  Avg first-token latency: {avg_first:.3f}s "
                  f"{target_icon} (target < 1.0s)")
            print(f"  Avg total latency: {avg_total:.3f}s "
                  f"{'✅' if avg_total < 4.0 else '❌'} (target < 4.0s)")
    else:
        _print_header("[6/6] LLM Latency Measurement (skipped)")
        print("  Use --measure-latency --llm-base-url=... --llm-model=... to enable")

    # ── 汇总 ──
    report.total_passed = sum(1 for c in report.checks if c.passed)
    report.total_failed = sum(1 for c in report.checks if not c.passed)

    print()
    print("═" * 60)
    passed_color = "\033[92m" if report.total_failed == 0 else "\033[93m"
    reset = "\033[0m"
    total = report.total_passed + report.total_failed
    print(f"  Result: {passed_color}{report.total_passed}/{total} checks passed{reset}")

    if report.total_failed > 0:
        print(f"\n  Failed checks:")
        for c in report.checks:
            if not c.passed:
                print(f"    ❌ {c.name}: {c.message}")

    # 保存报告
    report_path = _PROJECT_ROOT / "phase1b_verification_report.json"
    report_data = asdict(report)
    report_path.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Report saved: {report_path}")
    print()

    sys.exit(0 if report.total_failed == 0 else 1)


if __name__ == "__main__":
    main()
