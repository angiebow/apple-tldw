#!/usr/bin/env python3
"""
run_subtitles.py
=================
CLI entry point for generating a clip's subtitles from a full-podcast
transcript_words.json.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from subtitles import rebase_words, build_cues, write_srt


def parse_args():
    parser = argparse.ArgumentParser(description="Generate SRT subtitles for a clip.")
    parser.add_argument("--words", required=True, help="Path to *_transcript_words.json")
    parser.add_argument("--clip-start", type=float, required=True, help="Clip start, seconds (global timestamp)")
    parser.add_argument("--clip-end", type=float, required=True, help="Clip end, seconds (global timestamp)")
    parser.add_argument("--output", required=True, help="Output .srt path")
    parser.add_argument("--max-chars-per-line", type=int, default=42)
    parser.add_argument("--max-lines", type=int, default=2)
    parser.add_argument("--max-duration", type=float, default=7.0)
    parser.add_argument("--min-duration", type=float, default=1.0)
    parser.add_argument("--pause-break-threshold", type=float, default=0.4)
    parser.add_argument("--flag-confidence", type=float, default=0.6,
                         help="Cues with any word below this are flagged low_confidence (cosmetic only, nothing is dropped)")
    return parser.parse_args()


def main():
    args = parse_args()

    if not os.path.isfile(args.words):
        print(f"❌ Words file not found: {args.words}", file=sys.stderr)
        sys.exit(1)
    if args.clip_end <= args.clip_start:
        print(f"❌ --clip-end ({args.clip_end}) must be > --clip-start ({args.clip_start})", file=sys.stderr)
        sys.exit(1)

    with open(args.words, "r", encoding="utf-8") as f:
        words = json.load(f)

    if not words:
        print(f"❌ {args.words} contains no words", file=sys.stderr)
        sys.exit(1)

    clip_words = rebase_words(words, clip_start=args.clip_start, clip_end=args.clip_end)
    if not clip_words:
        print(f"⚠️  No words found in range [{args.clip_start}, {args.clip_end}] — check your timestamps", file=sys.stderr)
        sys.exit(1)

    cues = build_cues(
        clip_words,
        max_chars_per_line=args.max_chars_per_line,
        max_lines=args.max_lines,
        max_duration=args.max_duration,
        min_duration=args.min_duration,
        pause_break_threshold=args.pause_break_threshold,
        flag_confidence=args.flag_confidence,
    )

    flagged = sum(1 for c in cues if c.low_confidence)
    write_srt(cues, args.output)

    print(f"🎉 {len(cues)} cues generated ({flagged} flagged low-confidence)")


if __name__ == "__main__":
    main()