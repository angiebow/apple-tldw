#!/usr/bin/env python3
"""
run_subtitles.py
=================
CLI entry point for generating a clip's subtitles (SRT and/or karaoke ASS)
from a full-podcast transcript_words.json.
"""

import argparse
import json
import os
import sys

from subtitles import rebase_words, build_cues, write_srt
from subtitles.ass_writer import write_ass


def parse_args():
    parser = argparse.ArgumentParser(description="Generate SRT and/or karaoke ASS subtitles for a clip.")
    parser.add_argument("--words", required=True, help="Path to *_transcript_words.json")
    parser.add_argument("--clip-start", type=float, required=True, help="Clip start, seconds (global timestamp)")
    parser.add_argument("--clip-end", type=float, required=True, help="Clip end, seconds (global timestamp)")

    parser.add_argument("--output-srt", default=None, help="Output .srt path (omit to skip SRT generation)")
    parser.add_argument("--output-ass", default=None, help="Output .ass path (omit to skip karaoke generation)")

    parser.add_argument("--max-chars-per-line", type=int, default=42)
    parser.add_argument("--max-lines", type=int, default=2)
    parser.add_argument("--max-duration", type=float, default=7.0)
    parser.add_argument("--min-duration", type=float, default=1.0)
    parser.add_argument("--pause-break-threshold", type=float, default=0.4)
    parser.add_argument("--flag-confidence", type=float, default=0.6)

    parser.add_argument("--font-name", default="Arial", help="ASS karaoke font (ignored if --output-ass not set)")
    parser.add_argument("--font-size", type=int, default=22, help="Base font size for cue text (ASS only)")

    return parser.parse_args()


def main():
    args = parse_args()

    if not args.output_srt and not args.output_ass:
        print("❌ Specify at least one of --output-srt or --output-ass — nothing to generate otherwise", file=sys.stderr)
        sys.exit(1)

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

    # Cue grouping is shared by both outputs — built ONCE, reused for both
    # writers, rather than re-running build_cues twice. SRT and karaoke ASS
    # will therefore have identical cue boundaries/line-breaks; only the
    # rendering format differs.
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
    print(f"🎬 {len(cues)} cues generated ({flagged} flagged low-confidence)")

    if args.output_srt:
        write_srt(cues, args.output_srt)

    if args.output_ass:
        write_ass(cues, args.output_ass, font_name=args.font_name, font_size=args.font_size)

    if not args.output_srt and not args.output_ass:
        # unreachable given the early check above, kept as a defensive guard
        print("⚠️  Nothing was written.", file=sys.stderr)


if __name__ == "__main__":
    main()