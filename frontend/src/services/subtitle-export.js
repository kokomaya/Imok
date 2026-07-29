/**
 * 字幕导出工具 — 将转写条目导出为标准字幕文件（SRT / VTT）。
 *
 * 单一职责：把内存中的转写条目序列化为标准字幕文本，并触发下载。
 * 仅导出原文（不含译文，避免额外 LLM 资源消耗）。
 */

/**
 * 把秒数格式化为字幕时间戳。
 * @param {number} totalSeconds
 * @param {string} msSep 毫秒分隔符（SRT 用 ',' ，VTT 用 '.'）
 * @returns {string} HH:MM:SS,mmm
 */
function formatTimestamp(totalSeconds, msSep) {
  const t = Math.max(0, totalSeconds);
  const hh = Math.floor(t / 3600);
  const mm = Math.floor((t % 3600) / 60);
  const ss = Math.floor(t % 60);
  const ms = Math.round((t - Math.floor(t)) * 1000);
  const pad = (n, w = 2) => String(n).padStart(w, '0');
  return `${pad(hh)}:${pad(mm)}:${pad(ss)}${msSep}${pad(ms, 3)}`;
}

/**
 * 计算每条字幕的起止时间（秒）。
 * 优先使用后端提供的 segmentStart/segmentEnd；缺失时用 epoch 时间戳相对首条推算，
 * 再退化为按序号每条固定时长。
 * @param {Array} entries
 * @returns {{ start: number, end: number, text: string }[]}
 */
function buildCues(entries) {
  const DEFAULT_DUR = 3; // 无时间信息时每条默认 3 秒
  const baseEpoch = entries.find((e) => typeof e.epoch === 'number')?.epoch ?? null;

  return entries.map((e, i) => {
    let start;
    let end;

    if (typeof e.segmentStart === 'number' && typeof e.segmentEnd === 'number' && e.segmentEnd > e.segmentStart) {
      start = e.segmentStart;
      end = e.segmentEnd;
    } else if (baseEpoch != null && typeof e.epoch === 'number') {
      start = (e.epoch - baseEpoch) / 1000;
      const nextEpoch = entries[i + 1]?.epoch;
      end = typeof nextEpoch === 'number' ? (nextEpoch - baseEpoch) / 1000 : start + DEFAULT_DUR;
    } else {
      start = i * DEFAULT_DUR;
      end = start + DEFAULT_DUR;
    }

    if (end <= start) end = start + DEFAULT_DUR;
    return { start, end, text: (e.text || '').trim() };
  }).filter((c) => c.text);
}

/**
 * 导出为 SRT 文本（仅原文）。
 * @param {Array} entries 转写条目
 * @returns {string}
 */
export function toSRT(entries) {
  const cues = buildCues(entries);
  return cues
    .map((c, i) => {
      const start = formatTimestamp(c.start, ',');
      const end = formatTimestamp(c.end, ',');
      return `${i + 1}\n${start} --> ${end}\n${c.text}\n`;
    })
    .join('\n');
}

/**
 * 导出为 WebVTT 文本（仅原文）。
 * @param {Array} entries 转写条目
 * @returns {string}
 */
export function toVTT(entries) {
  const cues = buildCues(entries);
  const body = cues
    .map((c) => {
      const start = formatTimestamp(c.start, '.');
      const end = formatTimestamp(c.end, '.');
      return `${start} --> ${end}\n${c.text}\n`;
    })
    .join('\n');
  return `WEBVTT\n\n${body}`;
}

/**
 * 触发浏览器下载（Electron 渲染进程可用）。
 * @param {string} filename
 * @param {string} content
 * @param {string} [mime='text/plain']
 */
export function downloadTextFile(filename, content, mime = 'text/plain') {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * 导出字幕（原文）为指定格式并下载。
 * @param {Array} entries 转写条目
 * @param {'srt' | 'vtt'} format
 * @param {string} [baseName='subtitles']
 * @returns {boolean} 是否成功（无内容时返回 false）
 */
export function exportSubtitles(entries, format, baseName = 'subtitles') {
  if (!entries || entries.length === 0) return false;
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
  if (format === 'vtt') {
    downloadTextFile(`${baseName}-${stamp}.vtt`, toVTT(entries), 'text/vtt');
  } else {
    downloadTextFile(`${baseName}-${stamp}.srt`, toSRT(entries), 'application/x-subrip');
  }
  return true;
}
