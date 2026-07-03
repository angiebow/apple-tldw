"""
Group word-level timestamps into subtitle cues (line-grouped, duration-capped).
"""

from dataclasses import dataclass, field
from typing import Dict, List

SENTENCE_ENDERS = (".", "!", "?")


@dataclass
class SubtitleCue:
    index: int
    start: float
    end: float
    text: str
    words: List[Dict] = field(default_factory=list)  # raw word dicts — needed for karaoke (ASS) rendering
    low_confidence: bool = False


def build_cues(
    words: List[Dict],
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    max_duration: float = 7.0,
    min_duration: float = 1.0,
    pause_break_threshold: float = 0.4,
    flag_confidence: float = 0.6,
) -> List[SubtitleCue]:
    """
    Greedily pack words into cues. Never drops words — see prior notes on
    why dropping low-confidence words breaks grammar more visibly than
    displaying them as-is.

    Each cue now retains its raw word list (cue.words) in addition to the
    joined display text (cue.text). SRT rendering only needs cue.text;
    karaoke (ASS) rendering needs cue.words for per-word timing.
    """
    if not words:
        return []

    max_chars = max_chars_per_line * max_lines
    cues: List[SubtitleCue] = []
    buf: List[Dict] = []

    def flush(next_start: float = None):
        if not buf:
            return
        start = buf[0]["start"]
        end = max(buf[-1]["end"], start + min_duration)
        if next_start is not None:
            end = min(end, next_start)
            end = max(end, start + 0.05)
        text = _wrap_text(" ".join(w["word"].strip() for w in buf), max_chars_per_line)
        flagged = any(w.get("probability", 1.0) < flag_confidence for w in buf)
        cues.append(SubtitleCue(
            index=len(cues) + 1, start=start, end=end, text=text,
            words=list(buf), low_confidence=flagged,
        ))
        buf.clear()

    for i, w in enumerate(words):
        candidate_text = " ".join([*(x["word"].strip() for x in buf), w["word"].strip()])
        candidate_duration = w["end"] - (buf[0]["start"] if buf else w["start"])

        if buf and (len(candidate_text) > max_chars or candidate_duration > max_duration):
            flush(next_start=w["start"])

        buf.append(w)

        ends_sentence = w["word"].strip().endswith(SENTENCE_ENDERS)
        next_gap = (words[i + 1]["start"] - w["end"]) if i + 1 < len(words) else None
        pause_break = next_gap is not None and next_gap >= pause_break_threshold

        if ends_sentence or pause_break:
            flush(next_start=words[i + 1]["start"] if i + 1 < len(words) else None)

    flush()
    return cues


def _wrap_text(text: str, max_chars_per_line: int) -> str:
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars_per_line and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)