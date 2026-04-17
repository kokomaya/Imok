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
    'electron/**/*',
    'dist/**/*',
    '!node_modules/**/*',
  ],

  extraResources: [
    {
      from: '../backend',
      to: 'backend',
      filter: [
        '**/*.py',
        '!__pycache__/**',
        '!*.pyc',
      ],
    },
    {
      from: '../config',
      to: 'config',
      filter: ['**/*', '!*.yaml'],
    },
  ],

  win: {
    target: [
      {
        target: 'nsis',
        arch: ['x64'],
      },
    ],
    icon: 'build/icon.ico',
  },

  nsis: {
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    installerIcon: 'build/icon.ico',
    uninstallerIcon: 'build/icon.ico',
  },

  // macOS 配置（可选，当前主要面向 Windows）
  mac: {
    target: ['dmg'],
    icon: 'build/icon.icns',
  },
};
