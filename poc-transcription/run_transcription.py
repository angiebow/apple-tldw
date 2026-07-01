#!/usr/bin/env python3
"""
run_transcription.py
=====================
CLI entry point for the single-pass Whisper transcription pipeline.
"""

import argparse
import os
import sys
import time
from pathlib import Path

from audio_stt.config import WhisperConfig
from audio_stt.transcriber import WhisperTranscriber


def parse_args():
    parser = argparse.ArgumentParser(description="Run single-pass MLX Whisper STT.")
    parser.add_argument("--audio", default="input/hot-ones_preprocessed.wav")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--model", default="mlx-community/whisper-large-v3-turbo")
    parser.add_argument("--language", default="en")
    parser.add_argument("--sanitize-min-duration", type=float, default=0.01)
    parser.add_argument("--sanitize-max-segments-per-second", type=float, default=8.0)
    parser.add_argument("--sanitize-duplicate-timestamp-threshold", type=int, default=5)
    return parser.parse_args()


def main():
    args = parse_args()

    if not os.path.isfile(args.audio):
        print(f"❌ Audio file not found: {args.audio}", file=sys.stderr)
        sys.exit(1)

    config = WhisperConfig(
        model=args.model,
        language=args.language,
        sanitize_min_duration=args.sanitize_min_duration,
        sanitize_max_segments_per_second=args.sanitize_max_segments_per_second,
        sanitize_duplicate_timestamp_threshold=args.sanitize_duplicate_timestamp_threshold,
    )

    transcriber = WhisperTranscriber(audio_path=args.audio, config=config)

    pipeline_start = time.perf_counter()
    segments, words = transcriber.run()
    elapsed = time.perf_counter() - pipeline_start

    audio_stem = Path(args.audio).stem
    base_name = audio_stem[:-13] if audio_stem.endswith("_preprocessed") else audio_stem

    transcriber.save(segments, words, output_dir=args.output_dir, base_name=base_name)

    print(f"\n🎉 Done — {len(segments)} segments, {len(words)} words.")
    print(f"⏱️  Total transcription time: {elapsed:.2f}s ({elapsed / 60:.2f} min)")


if __name__ == "__main__":
    main()