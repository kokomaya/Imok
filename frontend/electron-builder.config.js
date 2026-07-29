/**
 * electron-builder 打包配置。
 *
 * 单一职责：定义 Electron 应用的打包和分发参数。
 * 参考文档：https://www.electron.build/configuration
 */

module.exports = {
  appId: 'com.imok.meeting-assistant',
  productName: 'Imok Meeting Assistant',
  copyright: 'Copyright © 2026',

  directories: {
    output: 'out',
    buildResources: 'build',
  },

  files: [
    '**/*',
    '!out/**/*',
    '!out-lite/**/*',
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
    // PyInstaller 打包的 Python 后端
    {
      from: '../dist/imok-backend',
      to: 'python-backend',
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
        '!llm_providers.yaml',   // 排除真实配置（含 API 地址）
      ],
    },
    // .env.example 供用户参考
    {
      from: '../.env.example',
      to: '.env.example',
    },
    // 帮助文档
    {
      from: '../docs',
      to: 'docs',
      filter: ['**/*.md'],
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

  // NSIS 不支持 >2GB 的应用包（32位 mmap 限制），
  // 因此使用 dir 目标生成便携版，打包后手动压缩分发。

  // macOS 配置（可选，当前主要面向 Windows）
  mac: {
    target: ['dmg'],
  },
};
