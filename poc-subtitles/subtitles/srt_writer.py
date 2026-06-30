"""
Write SubtitleCue objects to a .srt file using the `srt` library.
"""

from datetime import timedelta
from pathlib import Path
from typing import List

import srt

from .cue_builder import SubtitleCue


def write_srt(cues: List[SubtitleCue], output_path: str) -> str:
    """
    Convert SubtitleCue objects to srt.Subtitle objects and write to disk.

    NOTE: srt.compose() re-sorts and re-indexes by start time internally
    if you pass reindex=True (default). Our cues are already in order, but
    if any caller ever passes cues out of order, this will silently
    reorder them rather than erroring — worth knowing if cue indices ever
    look unexpectedly shuffled.
    """
    subtitles = [
        srt.Subtitle(
            index=cue.index,
            start=timedelta(seconds=cue.start),
            end=timedelta(seconds=cue.end),
            content=cue.text,
        )
        for cue in cues
    ]

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(srt.compose(subtitles), encoding="utf-8")

    print(f"📄 Subtitles: {out}")
    return str(out)