"""
Transcript Sanitization
========================
Post-processes raw Whisper output to detect and strip hallucination
artifacts before they reach transcript_segments.json / transcript_words.json.

Whisper has no first-class "this is a hallucination" signal — these are
heuristic detectors for known failure shapes, run as independent passes.
Each pass is conservative by design: when in doubt, keep the segment. A
missed hallucination is recoverable (a human catches a bad subtitle); a
falsely dropped real segment is a silent data-loss bug that's much harder
to notice.
"""

import re
from typing import Dict, List, Tuple

_CHAR_LOOP_PATTERN = re.compile(r"(.{1,4})\1{3,}", re.IGNORECASE)


class TranscriptSanitizer:
    """
    Runs a sequence of hallucination-detection passes over raw Whisper
    segment dicts.

    Example:
        >>> sanitizer = TranscriptSanitizer(min_duration=0.01, max_segments_per_second=8.0)
        >>> clean, dropped = sanitizer.run(raw_segments)
    """

    def __init__(
        self,
        min_duration: float = 0.01,
        max_segments_per_second: float = 8.0,
        duplicate_timestamp_run_threshold: int = 5,
    ):
        self.min_duration = min_duration
        self.max_segments_per_second = max_segments_per_second
        self.duplicate_timestamp_run_threshold = duplicate_timestamp_run_threshold

    def run(self, segments: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """
        Run all sanitization passes on raw segment dicts. Each dict must
        have 'id', 'start', 'end', 'text'.

        Returns (clean_segments, dropped_segments). dropped_segments retain
        their original 'id' plus a '_reason' field — never silently discard
        without keeping a record.
        """
        dropped: List[Dict] = []

        segments, d1 = self._drop_empty_or_zero_duration(segments)
        dropped.extend(d1)

        segments, d2 = self._drop_duplicate_timestamp_runs(segments)
        dropped.extend(d2)

        segments, d3 = self._drop_char_loop_segments(segments)
        dropped.extend(d3)

        segments, d4 = self._drop_density_explosion_windows(segments)
        dropped.extend(d4)

        return segments, dropped

    def _drop_empty_or_zero_duration(self, segments: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """Pass 1: strip segments with empty/whitespace-only text or
        near-zero duration. Cheapest, highest-confidence pass — run first."""
        kept, dropped = [], []
        for s in segments:
            text = s.get("text", "").strip()
            duration = s["end"] - s["start"]
            if not text or duration < self.min_duration:
                dropped.append({**s, "_reason": "empty_or_zero_duration"})
            else:
                kept.append(s)
        return kept, dropped

    def _drop_duplicate_timestamp_runs(self, segments: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """Pass 2: N+ consecutive segments sharing an identical start
        timestamp is a decoder stall, not real speech. Keeps one
        representative segment from the run, drops the rest."""
        if not segments:
            return segments, []

        kept, dropped = [], []
        run_start_idx = 0
        threshold = self.duplicate_timestamp_run_threshold
        for i in range(1, len(segments) + 1):
            same_ts = i < len(segments) and segments[i]["start"] == segments[run_start_idx]["start"]
            if not same_ts:
                run = segments[run_start_idx:i]
                if len(run) >= threshold:
                    kept.append(run[0])
                    dropped.extend([{**s, "_reason": "duplicate_timestamp_run"} for s in run[1:]])
                else:
                    kept.extend(run)
                run_start_idx = i
        return kept, dropped

    def _drop_char_loop_segments(self, segments: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """Pass 3: catches in-text repetition loops that survive pass 1."""
        kept, dropped = [], []
        for s in segments:
            if _CHAR_LOOP_PATTERN.search(s.get("text", "")):
                dropped.append({**s, "_reason": "char_repetition_loop"})
            else:
                kept.append(s)
        return kept, dropped

    def _drop_density_explosion_windows(self, segments: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """Pass 4: sliding 1-second window segment-count cap. Safety net for
        collapse patterns that don't match the specific shapes above — real
        speech doesn't produce more than a handful of segments per second."""
        if not segments:
            return segments, []

        kept, dropped = [], []
        i = 0
        max_per_second = self.max_segments_per_second
        while i < len(segments):
            window_end_time = segments[i]["start"] + 1.0
            j = i
            while j < len(segments) and segments[j]["start"] < window_end_time:
                j += 1
            window = segments[i:j]
            if len(window) > max_per_second:
                kept.append(window[0])
                dropped.extend([{**s, "_reason": "density_explosion"} for s in window[1:]])
            else:
                kept.extend(window)
            i = j
        return kept, dropped