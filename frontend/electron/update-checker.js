/**
 * 版本更新检查模块。
 *
 * 单一职责：请求 GitHub Releases API，对比本地版本号，返回更新信息。
 * 不涉及任何 UI 操作。
 */

const https = require('https');

const REPO_OWNER = 'kokomaya';
const REPO_NAME = 'Imok';
const API_URL = `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/releases/latest`;

/**
 * 简易 semver 比较：latest > current 则返回 true。
 * 仅支持 x.y.z 格式（忽略 pre-release 标签）。
 */
function isNewer(latest, current) {
  const parse = (v) => v.replace(/^v/, '').split('.').map(Number);
  const [lMaj, lMin, lPat] = parse(latest);
  const [cMaj, cMin, cPat] = parse(current);
  if (lMaj !== cMaj) return lMaj > cMaj;
  if (lMin !== cMin) return lMin > cMin;
  return lPat > cPat;
}

/**
 * 检查是否有新版本可用。
 * @param {string} currentVersion - 当前版本号（如 "0.1.0"）
 * @returns {Promise<{ hasUpdate: boolean, latest?: string, url?: string, releaseNotes?: string } | null>}
 */
function checkForUpdate(currentVersion) {
  return new Promise((resolve) => {
    const req = https.get(API_URL, {
      headers: {
        'User-Agent': `Imok/${currentVersion}`,
        'Accept': 'application/vnd.github.v3+json',
      },
      timeout: 10000,
    }, (res) => {
      if (res.statusCode === 404) {
        // No releases yet
        resolve({ hasUpdate: false });
        return;
      }
      if (res.statusCode !== 200) {
        resolve(null);
        return;
      }

      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        try {
          const data = JSON.parse(body);
          const latest = (data.tag_name || '').replace(/^v/, '');
          if (!latest) { resolve(null); return; }

          resolve({
            hasUpdate: isNewer(latest, currentVersion),
            latest,
            url: data.html_url || '',
            releaseNotes: data.body || '',
          });
        } catch {
          resolve(null);
        }
      });
    });

    req.on('error', () => resolve(null));
    req.on('timeout', () => { req.destroy(); resolve(null); });
  });
}

module.exports = { checkForUpdate, isNewer };
