"""Whisper 幻觉短语过滤 — 从 meeting_pipeline.py 提取。

Whisper 在处理低信噪比或接近静音的音频段时，容易生成训练集中高频出现的短语。
此模块提供 is_hallucination() 检测函数。
"""

from __future__ import annotations

import logging

from backend.asr.base import TranscriptionResult

logger = logging.getLogger(__name__)

_HALLUCINATION_PHRASES: frozenset[str] = frozenset(
    p.lower()
    for p in [
        "Thanks for watching!",
        "Thank you for watching!",
        "Thank you for watching.",
        "Thanks for watching.",
        "Subscribe to my channel",
        "Please subscribe",
        "Like and subscribe",
        "See you next time",
        "See you in the next video",
        "Bye bye",
        "Bye bye!",
        "Bye-bye",
        "Thank you.",
        "Thank you!",
        "Thanks.",
        "You",
        "you",
        "...",
        "MBC 뉴스 이덕영입니다.",
        "ご視聴ありがとうございました",
        "字幕by索兰娅",
        "字幕由Amara.org社区提供",
        "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
    ]
)

# 高 no_speech_prob 表示 Whisper 自己也认为这段没有语音
# 注意：系统音频（loopback）经常有较高的 no_speech_prob，阈值不宜太低
_NO_SPEECH_PROB_THRESHOLD = 0.8

# 低 avg_logprob 表示模型对自己的输出不自信
_LOW_CONFIDENCE_LOGPROB = -1.5


def is_hallucination(result: TranscriptionResult) -> bool:
    """检测 Whisper 幻觉输出。"""
    text = result.text.strip().lower()

    # 精确匹配已知幻觉短语
    if text in _HALLUCINATION_PHRASES:
        return True

    # 检查每个 segment 的 no_speech_prob 和 avg_logprob
    if result.segments:
        avg_no_speech = sum(s.no_speech_prob for s in result.segments) / len(result.segments)
        avg_logprob = sum(s.avg_logprob for s in result.segments) / len(result.segments)

        # 高 no_speech_prob + 已知幻觉短语模式
        if avg_no_speech > _NO_SPEECH_PROB_THRESHOLD:
            logger.debug(
                "Filtered likely hallucination (no_speech=%.2f): '%s'",
                avg_no_speech, text[:60],
            )
            return True

        # 极低置信度 + 非常短的文本 → 大概率是幻觉
        if avg_logprob < _LOW_CONFIDENCE_LOGPROB and len(text) < 15:
            logger.debug(
                "Filtered low-confidence short text (logprob=%.2f): '%s'",
                avg_logprob, text[:60],
            )
            return True

    return False
