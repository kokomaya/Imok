/**
 * electron-builder 打包配置 — 轻量版（不含 Python 运行环境）。
 *
 * 用户需自行安装 Python 3.12+ 并通过 pip install -r requirements.txt 安装依赖。
 * 参考文档：https://www.electron.build/configuration
 */

module.exports = {
  appId: 'com.imok.meeting-assistant',
  productName: 'Imok Meeting Assistant',
  copyright: 'Copyright © 2026',

  directories: {
    output: 'out-lite',
    buildResources: 'build',
  },

  files: [
    '**/*',
    '!node_modules/**/*',
    '!src/**/*',
    '!public/**/*',
    '!*.config.*',
    '!*.ts',
    '!tsconfig*',
    '!.eslint*',
    '!.prettier*',
  ],

  extraResources: [
    // Python 后端源码（用户自行安装 Python 环境）
    {
      from: '../backend',
      to: 'backend',
      filter: ['**/*.py', '!__pycache__/**', '!**/*.pyc'],
    },
    // scripts/detect_gpu.py — 被 backend.config 动态导入
    {
      from: '../scripts/detect_gpu.py',
      to: 'scripts/detect_gpu.py',
    },
    // 说话人识别预训练模型
    {
      from: '../pretrained_models',
      to: 'pretrained_models',
      filter: ['**/*'],
    },
    // 配置文件（仅 example 和非敏感文件）
    {
      from: '../config',
      to: 'config',
      filter: [
        '**/*',
        '!llm_providers.yaml',
      ],
    },
    // requirements.txt — 用户安装依赖用
    {
      from: '../requirements.txt',
      to: 'requirements.txt',
    },
    // .env.example 供用户参考
    {
      from: '../.env.example',
      to: '.env.example',
    },
    // 安装说明
    {
      from: '../INSTALL.md',
      to: 'INSTALL.md',
    },
  ],

  win: {
    target: [
      {
        target: 'dir',
        arch: ['x64'],
      },
    ],
    signAndEditExecutable: false,
  },

  mac: {
    target: ['dmg'],
  },
};
