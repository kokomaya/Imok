/**
 * Python 子进程管理器 — Electron 主进程与 Python 后端的 IPC 桥接。
 *
 * 单一职责：管理 Python 子进程生命周期和 JSON Lines 通信。
 * 不负责 UI 渲染、LLM 调用或业务逻辑。
 *
 * 通信协议：
 *   stdout (Python → Electron): JSON Lines, 每行一个 JSON
 *   stdin  (Electron → Python): JSON Lines 控制命令
 *
 * 使用方式：
 *   const bridge = new PythonBridge({ pythonPath: 'python', scriptArgs: ['--source=wasapi'] });
 *   bridge.on('transcription', (data) => { ... });
 *   bridge.on('status', (data) => { ... });
 *   bridge.on('error', (data) => { ... });
 *   bridge.start();
 *   bridge.sendControl('start');
 *   bridge.sendControl('stop');
 *   bridge.destroy();
 */

const { spawn } = require('child_process');
const { EventEmitter } = require('events');
const path = require('path');

/**
 * @typedef {Object} PythonBridgeOptions
 * @property {string} [pythonPath='python'] - Python 可执行文件路径
 * @property {string} [backendDir] - backend 目录路径（默认自动检测）
 * @property {string} [source='wasapi'] - 音频源 (wasapi | mic)
 * @property {string} [logLevel='INFO'] - Python 日志级别
 * @property {number} [restartDelayMs=2000] - 异常退出后重启延迟（毫秒）
 * @property {number} [maxRestarts=5] - 最大连续重启次数
 * @property {boolean} [bundled=false] - 是否使用打包后的 exe（无需 -m backend.main）
 * @property {string} [projectRoot] - 项目根目录（传递给 Python 的 IMOK_PROJECT_ROOT）
 */

class PythonBridge extends EventEmitter {
  /**
   * @param {PythonBridgeOptions} options
   */
  constructor(options = {}) {
    super();

    this._pythonPath = options.pythonPath || 'python';
    this._backendDir = options.backendDir || path.resolve(__dirname, '..', '..');
    this._source = options.source || 'wasapi';
    this._logLevel = options.logLevel || 'INFO';
    this._bundled = !!options.bundled;
    this._projectRoot = options.projectRoot || this._backendDir;
    this._restartDelayMs = options.restartDelayMs ?? 2000;
    this._maxRestarts = options.maxRestarts ?? 5;

    /** @type {import('child_process').ChildProcess | null} */
    this._process = null;
    this._running = false;
    this._destroyed = false;
    this._restartCount = 0;
    this._restartTimer = null;
    this._lineBuffer = '';
  }

  // ------------------------------------------------------------------
  // 生命周期
  // ------------------------------------------------------------------

  /**
   * 启动 Python 子进程。
   */
  start() {
    if (this._destroyed) {
      throw new Error('PythonBridge has been destroyed');
    }
    if (this._process) {
      return; // 已在运行
    }

    this._running = true;
    this._spawn();
  }

  /**
   * 销毁桥接，终止子进程，不再自动重启。
   */
  destroy() {
    this._destroyed = true;
    this._running = false;

    if (this._restartTimer) {
      clearTimeout(this._restartTimer);
      this._restartTimer = null;
    }

    if (this._process) {
      // 先尝试优雅退出：发送 stop 命令
      try {
        this.sendControl('stop');
      } catch (_) {
        // ignore
      }
      // 给 Python 一点时间优雅退出
      setTimeout(() => {
        if (this._process) {
          this._process.kill('SIGTERM');
          this._process = null;
        }
      }, 1000);
    }

    this.removeAllListeners();
  }

  // ------------------------------------------------------------------
  // 控制命令
  // ------------------------------------------------------------------

  /**
   * 发送控制命令到 Python 子进程 stdin。
   * @param {'start' | 'stop' | 'switch_source'} action
   * @param {Object} [extra] - 附加参数，如 { source: 'mic' }
   */
  sendControl(action, extra = {}) {
    if (!this._process || !this._process.stdin || this._process.stdin.destroyed) {
      console.warn(`[PythonBridge] sendControl(${action}) FAILED — stdin unavailable, process=${!!this._process}`);
      this.emit('error', { code: 'stdin_unavailable', message: 'Python process stdin not available' });
      return;
    }

    const message = {
      type: 'control',
      data: { action, ...extra },
      ts: Date.now() / 1000,
    };

    try {
      const json = JSON.stringify(message);
      console.log(`[PythonBridge] sendControl → stdin: ${json}`);
      this._process.stdin.write(json + '\n');
    } catch (err) {
      console.error(`[PythonBridge] sendControl(${action}) write error:`, err.message);
      this.emit('error', { code: 'stdin_write_error', message: err.message });
    }
  }

  // ------------------------------------------------------------------
  // 状态查询
  // ------------------------------------------------------------------

  /** @returns {boolean} */
  get isRunning() {
    return this._running && this._process !== null;
  }

  /** @returns {number} Python 子进程 PID，未运行返回 -1 */
  get pid() {
    return this._process ? this._process.pid : -1;
  }

  // ------------------------------------------------------------------
  // 内部方法
  // ------------------------------------------------------------------

  /** @private */
  _spawn() {
    const args = this._bundled
      ? [
          '--mode=subprocess',
          `--source=${this._source}`,
          `--log-level=${this._logLevel}`,
        ]
      : [
          '-m', 'backend.main',
          '--mode=subprocess',
          `--source=${this._source}`,
          `--log-level=${this._logLevel}`,
        ];

    console.log(`[PythonBridge] _spawn() pythonPath=${this._pythonPath} args=${JSON.stringify(args)} cwd=${this._backendDir} bundled=${this._bundled}`);

    this._process = spawn(this._pythonPath, args, {
      cwd: this._backendDir,
      stdio: ['pipe', 'pipe', 'pipe'],
      // Windows 上不使用 shell，避免额外的 cmd.exe 包装
      shell: false,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        IMOK_PROJECT_ROOT: this._projectRoot,
      },
    });

    console.log(`[PythonBridge] spawned pid=${this._process.pid}`);

    this._lineBuffer = '';

    // stdout: 按行读取 JSON Lines
    this._process.stdout.setEncoding('utf-8');
    this._process.stdout.on('data', (chunk) => this._onStdoutData(chunk));

    // stderr: 转发 Python 日志
    this._process.stderr.setEncoding('utf-8');
    this._process.stderr.on('data', (chunk) => {
      this.emit('log', chunk.trimEnd());
    });

    // 进程退出
    this._process.on('close', (code, signal) => {
      console.log(`[PythonBridge] process exited code=${code} signal=${signal}`);
      this._process = null;
      this.emit('exit', { code, signal });

      if (this._running && !this._destroyed) {
        this._handleUnexpectedExit(code, signal);
      }
    });

    // 进程错误（如 Python 不存在）
    this._process.on('error', (err) => {
      console.error(`[PythonBridge] spawn error:`, err.message);
      this.emit('error', { code: 'spawn_error', message: err.message });
      this._process = null;
    });
  }

  /**
   * 处理 stdout 数据：按 \n 分割成行，每行解析为 JSON 消息。
   * @private
   * @param {string} chunk
   */
  _onStdoutData(chunk) {
    this._lineBuffer += chunk;
    const lines = this._lineBuffer.split('\n');
    // 最后一个元素可能是不完整的行，保留在 buffer 中
    this._lineBuffer = lines.pop() || '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      try {
        const message = JSON.parse(trimmed);
        this._dispatchMessage(message);
      } catch (err) {
        this.emit('error', {
          code: 'json_parse_error',
          message: `Failed to parse: ${trimmed.substring(0, 100)}`,
        });
      }
    }
  }

  /**
   * 分发已解析的 IPC 消息。
   * @private
   * @param {{ type: string, data: object, ts: number }} message
   */
  _dispatchMessage(message) {
    const { type, data } = message;

    console.log(`[PythonBridge] ← msg type=${type} data=${JSON.stringify(data).substring(0, 200)}`);

    switch (type) {
      case 'transcription':
        this.emit('transcription', data);
        break;
      case 'transcription_partial':
        this.emit('transcription-partial', data);
        break;
      case 'status':
        this.emit('status', data);
        break;
      case 'error':
        this.emit('python-error', data);
        break;
      case 'segment_summary':
        this.emit('segment-summary', data);
        break;
      case 'global_summary':
        this.emit('global-summary', data);
        break;
      case 'audio_level':
        this.emit('audio-level', data);
        break;
      default:
        this.emit('message', message);
    }
  }

  /**
   * 处理非预期退出：延迟重启。
   * @private
   */
  _handleUnexpectedExit(code, signal) {
    this._restartCount++;

    if (this._restartCount > this._maxRestarts) {
      this.emit('error', {
        code: 'max_restarts_exceeded',
        message: `Python process crashed ${this._restartCount} times, giving up.`,
      });
      this._running = false;
      return;
    }

    this.emit('restart', {
      attempt: this._restartCount,
      maxRestarts: this._maxRestarts,
      delayMs: this._restartDelayMs,
      lastExitCode: code,
    });

    this._restartTimer = setTimeout(() => {
      this._restartTimer = null;
      if (this._running && !this._destroyed) {
        this._spawn();
      }
    }, this._restartDelayMs);
  }

  /**
   * 重置重启计数器（首次收到 READY 状态时调用）。
   */
  resetRestartCount() {
    this._restartCount = 0;
  }
}

module.exports = { PythonBridge };
