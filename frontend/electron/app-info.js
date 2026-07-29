/**
 * 应用元数据模块。
 *
 * 单一职责：读取 package.json 并导出标准化的应用信息对象。
 * 不依赖 Electron API，纯数据导出。
 */

const path = require('path');

function getAppInfo() {
  const pkg = require(path.join(__dirname, '..', 'package.json'));
  return {
    name: pkg.name,
    displayName: 'Imok',
    version: pkg.version,
    description: pkg.description,
    author: { name: 'kokomaya', url: 'https://github.com/kokomaya' },
    repository: 'https://github.com/kokomaya/Imok',
    license: 'MIT',
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
    v8: process.versions.v8,
  };
}

module.exports = { getAppInfo };
