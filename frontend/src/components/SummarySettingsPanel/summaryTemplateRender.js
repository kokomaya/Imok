/**
 * 会议总结模板渲染 — 把结构化模板渲染为 LLM system prompt。
 *
 * 单一职责：模板 → prompt 文本 的纯函数转换，前后端共用同一套规则。
 * 后端（live）通过 IPC 接收渲染结果；前端（回看/降级）直接使用。
 *
 * 设计约束：内置 4 个固定章节 key（topics/conclusions/actions/risks），
 * 用户可开关、重命名标题、补充额外指令，或切换到 advanced 模式直接编辑完整 prompt。
 * 固定 key 保证结构化解析（主题/结论/待办 标签页）始终可用。
 */

import {
  SUMMARY_SYSTEM_PROMPT,
  MERGE_SYSTEM_PROMPT,
} from '@/prompts/index.js';

/** 内置章节定义（顺序即默认展示顺序）。 */
export const DEFAULT_SECTIONS = [
  { key: 'topics', title: '主题', enabled: true },
  { key: 'conclusions', title: '结论', enabled: true },
  { key: 'actions', title: 'Action Items', enabled: true },
  { key: 'risks', title: '风险', enabled: true },
];

export const SECTION_META = {
  topics: { label: '主题', requirement: '提取讨论主题' },
  conclusions: { label: '结论', requirement: '提炼关键结论与技术决策' },
  actions: { label: 'Action Items / 待办', requirement: '识别 Action Items（含责任人和截止时间，如有提及）' },
  risks: { label: '风险', requirement: '标注重要的风险项' },
};

/** 创建一个全新的空白模板（章节全开、默认标题）。
 * 高级模式的完整 Prompt 预填系统默认内容，供用户在其上扩展（而非留空）。 */
export function createBlankTemplate(name = '新模板') {
  return {
    id: `tpl_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    name,
    mode: 'sections',
    sections: DEFAULT_SECTIONS.map((s) => ({ ...s })),
    extraInstructions: '',
    advancedSegmentSystem: SUMMARY_SYSTEM_PROMPT,
    advancedMergeSystem: MERGE_SYSTEM_PROMPT,
  };
}

/** 归一化任意来源的模板对象为完整合法结构。 */
export function normalizeTemplate(raw) {
  const base = createBlankTemplate(raw?.name || '未命名模板');
  if (!raw || typeof raw !== 'object') return base;
  const byKey = {};
  for (const s of Array.isArray(raw.sections) ? raw.sections : []) {
    if (s && SECTION_META[s.key]) {
      byKey[s.key] = {
        key: s.key,
        title: String(s.title || SECTION_META[s.key].label).trim() || DEFAULT_SECTIONS.find((d) => d.key === s.key).title,
        enabled: s.enabled !== false,
      };
    }
  }
  const sections = DEFAULT_SECTIONS.map((d) => byKey[d.key] || { ...d });
  return {
    id: String(raw.id || base.id),
    name: String(raw.name || base.name).trim() || base.name,
    mode: raw.mode === 'advanced' ? 'advanced' : 'sections',
    sections,
    extraInstructions: String(raw.extraInstructions || ''),
    advancedSegmentSystem: String(raw.advancedSegmentSystem || ''),
    advancedMergeSystem: String(raw.advancedMergeSystem || ''),
  };
}

function enabledSections(template) {
  return (template.sections || []).filter((s) => s.enabled);
}

/** 渲染段落摘要 system prompt（sections 模式）。 */
function renderSegmentSections(template) {
  const secs = enabledSections(template);
  if (!secs.length) return SUMMARY_SYSTEM_PROMPT;

  const req = [];
  for (const s of secs) req.push(SECTION_META[s.key].requirement);
  req.push('如果文本中包含 [Speaker_X] 标签，请在结论和 Action Items 中标注发言人');
  req.push('使用简洁的条目式输出，不要使用表格');
  const extra = (template.extraInstructions || '').trim();
  if (extra) req.push(extra);

  const reqBlock = req.map((r, i) => `${i + 1}. ${r}`).join('\n');
  const fmtBlock = secs.map((s) => `## ${s.title}\n- ...`).join('\n\n');

  return `你是一个专业的会议记录助手。你的任务是对会议转写文本进行结构化摘要。

要求：
${reqBlock}

严格按照以下格式输出（使用 - 列表，不要使用表格）：

${fmtBlock}`;
}

/** 渲染全局合并 system prompt（sections 模式）。 */
function renderMergeSections(template) {
  const secs = enabledSections(template);
  if (!secs.length) return MERGE_SYSTEM_PROMPT;

  const req = [
    '合并相同主题，去除重复内容',
    '按讨论顺序组织内容',
    '保留所有 Action Items，不要遗漏',
  ];
  const extra = (template.extraInstructions || '').trim();
  if (extra) req.push(extra);

  const reqBlock = req.map((r, i) => `${i + 1}. ${r}`).join('\n');
  const fmtBlock = secs.map((s) => `## ${s.title}\n- ...`).join('\n\n');

  return `你是一个专业的会议记录助手。你的任务是将多个段落摘要合并为一份结构化的全局会议总结。

要求：
${reqBlock}

严格按照以下格式输出（使用 - 列表，不要使用表格）：

${fmtBlock}`;
}

/**
 * 渲染模板 → { segmentSystem, mergeSystem, sectionTitles }。
 * sectionTitles 供结构化解析感知自定义标题。
 * @param {Object} template
 * @returns {{ segmentSystem: string, mergeSystem: string, sectionTitles: Object }}
 */
export function renderTemplate(template) {
  if (!template) {
    return { segmentSystem: SUMMARY_SYSTEM_PROMPT, mergeSystem: MERGE_SYSTEM_PROMPT, sectionTitles: {} };
  }

  if (template.mode === 'advanced') {
    return {
      segmentSystem: (template.advancedSegmentSystem || '').trim() || SUMMARY_SYSTEM_PROMPT,
      mergeSystem: (template.advancedMergeSystem || '').trim() || MERGE_SYSTEM_PROMPT,
      sectionTitles: {},
    };
  }

  const sectionTitles = {};
  for (const s of enabledSections(template)) sectionTitles[s.key] = s.title;

  return {
    segmentSystem: renderSegmentSections(template),
    mergeSystem: renderMergeSections(template),
    sectionTitles,
  };
}
