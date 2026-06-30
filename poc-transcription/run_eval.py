#!/usr/bin/env python3
"""
run_eval.py
============
CLI for scoring transcriber output against ground truth.

Usage:
    # Whole-transcript WER (reference as plain text file)
    python run_eval.py --hyp output/hot-ones_transcript_segments.json --ref-text input/hot-ones_ground_truth.txt

    # Per-segment WER (both files must have matching segment counts/order)
    python run_eval.py --hyp output/hot-ones_transcript_segments.json --ref-segments input/hot-ones_ref_segments.json --per-segment
"""

import argparse
import sys

from eval.metrics import (
    evaluate_against_reference,
    evaluate_segments_individually,
    print_report,
)


def parse_args():
    p = argparse.ArgumentParser(description="WER/CER eval for transcript output.")
    p.add_argument("--hyp", required=True, help="Path to transcript_segments.json (model output)")
    p.add_argument("--ref-text", help="Path to plain-text ground truth (whole-transcript WER)")
    p.add_argument("--ref-segments", help="Path to ground-truth segments JSON (per-segment WER)")
    p.add_argument("--per-segment", action="store_true", help="Report per-segment breakdown")
    p.add_argument("--worst-n", type=int, default=5, help="Show N worst segments when --per-segment")
    return p.parse_args()


def main():
    args = parse_args()

    if args.per_segment:
        if not args.ref_segments:
            print("❌ --per-segment requires --ref-segments", file=sys.stderr)
            sys.exit(1)
        results = evaluate_segments_individually(args.hyp, args.ref_segments)

        total_words = sum(r.ref_word_count for r in results)
        total_hits = sum(r.hits for r in results)
        agg_wer = 1 - (total_hits / total_words) if total_words else 0.0
        print(f"\n📊 Aggregate WER across {len(results)} segments: {agg_wer:.2%}")

        ranked = sorted(enumerate(results), key=lambda x: x[1].wer, reverse=True)
        print(f"\n🔻 Worst {args.worst_n} segments:")
        for idx, r in ranked[: args.worst_n]:
            print(f"   [{idx}] WER {r.wer:.2%}  (sub={r.substitutions} del={r.deletions} ins={r.insertions})")

    else:
        if not args.ref_text:
            print("❌ Whole-transcript mode requires --ref-text", file=sys.stderr)
            sys.exit(1)
        with open(args.ref_text, "r", encoding="utf-8") as f:
            reference = f.read()
        result = evaluate_against_reference(args.hyp, reference)
        print_report(result, label=args.hyp)


if __name__ == "__main__":
    main()