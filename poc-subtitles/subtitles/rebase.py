"""
Rebase global (full-podcast) word timestamps to clip-relative timestamps.
"""

from typing import Dict, List


def rebase_words(
    words: List[Dict],
    clip_start: float,
    clip_end: float,
) -> List[Dict]:
    """
    Filter words to those within [clip_start, clip_end] and shift their
    timestamps so clip_start becomes t=0.

    Words mostly cut off by the clip boundary (more than half their
    duration outside the range) are dropped rather than rendered as
    orphaned fragments.
    """
    if clip_end <= clip_start:
        raise ValueError(f"clip_end ({clip_end}) must be > clip_start ({clip_start})")

    rebased = []
    for w in words:
        if w["end"] <= clip_start or w["start"] >= clip_end:
            continue

        original_duration = w["end"] - w["start"]
        visible_duration = min(w["end"], clip_end) - max(w["start"], clip_start)
        if original_duration > 0 and visible_duration < original_duration * 0.5:
            continue

        start = max(w["start"], clip_start) - clip_start
        end = min(w["end"], clip_end) - clip_start

        rebased.append({**w, "start": round(start, 3), "end": round(end, 3)})

    return rebased