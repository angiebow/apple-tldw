#!/usr/bin/env python3
"""
run_transcription.py
=====================
CLI entry point for the Whisper transcription pipeline.
"""

import argparse
import os
import sys
import time
from pathlib import Path

from audio_stt.config import WhisperConfig
from audio_stt.transcriber import WhisperTranscriber


def parse_args():
    parser = argparse.ArgumentParser(description="Run MLX Whisper STT guided by VAD segments.")
    parser.add_argument("--audio", default="input/hot-ones_preprocessed.wav")
    parser.add_argument("--vad", default="input/hot-ones_vad_metadata.json")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--model", default="mlx-community/whisper-large-v3-turbo")
    parser.add_argument("--max-chunk-duration", type=float, default=45.0)
    parser.add_argument("--max-merge-gap", type=float, default=1.0)
    return parser.parse_args()


def main():
    args = parse_args()

    if not os.path.isfile(args.audio):
        print(f"❌ Audio file not found: {args.audio}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(args.vad):
        print(f"❌ VAD metadata file not found: {args.vad}", file=sys.stderr)
        sys.exit(1)

    config = WhisperConfig(
        model=args.model,
        max_chunk_duration=args.max_chunk_duration,
        max_merge_gap=args.max_merge_gap,
    )

    transcriber = WhisperTranscriber(audio_path=args.audio, vad_path=args.vad, config=config)

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