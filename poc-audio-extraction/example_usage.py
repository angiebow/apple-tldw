#!/usr/bin/env python3
"""
Example Usage Script
====================
Demonstrates how to use the AudioPreprocessor module in a real workflow.

Run with:
    python example_usage.py path/to/podcast.mp4

Or with custom configuration:
    python example_usage.py path/to/podcast.mp4 --output-dir my_output/ --log-level DEBUG
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audio preprocessing pipeline for podcast videos.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "input",
        type=str,
        help="Path to the input video/audio file (mp4, mov, wav, mp3, …)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Directory where all output artefacts are written.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Python logging level.",
    )
    parser.add_argument(
        "--snr-clean",
        type=float,
        default=20.0,
        help="SNR threshold (dB) above which audio is considered clean.",
    )
    parser.add_argument(
        "--snr-mild",
        type=float,
        default=10.0,
        help="SNR threshold (dB) below which strong denoising is applied.",
    )
    parser.add_argument(
        "--vad-threshold",
        type=float,
        default=0.5,
        help="Silero VAD speech probability threshold (0–1).",
    )
    parser.add_argument(
        "--force-denoise",
        action="store_true",
        help="Force denoising regardless of audio quality assessment.",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    # ── Import here so the module is resolved relative to this script's location.
    # Add the project root to sys.path if running from a cloned repo without install.
    project_root = str(Path(__file__).parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from audio_preprocessor import AudioPreprocessor
    from audio_preprocessor.config import (
        DenoiseConfig,
        FFmpegConfig,
        PreprocessorConfig,
        QualityConfig,
        VADConfig,
    )

    # ── Build configuration ───────────────────────────────────────────── #
    config = PreprocessorConfig(
        output_dir=args.output_dir,
        log_level=args.log_level,
        ffmpeg=FFmpegConfig(
            sample_rate=16_000,
            channels=1,
            codec="pcm_s16le",
        ),
        quality=QualityConfig(),
        denoise=DenoiseConfig(
            snr_clean_threshold=args.snr_clean,
            snr_mild_threshold=args.snr_mild,
            force_denoise=args.force_denoise,
        ),
        vad=VADConfig(
            threshold=args.vad_threshold,
            min_speech_duration_ms=250,
            min_silence_duration_ms=500,
        ),
    )

    # ── Run the pipeline ──────────────────────────────────────────────── #
    preprocessor = AudioPreprocessor(config)

    print(f"\n{'─' * 60}")
    print(f"  Input  : {args.input}")
    print(f"  Output : {os.path.abspath(args.output_dir)}")
    print(f"{'─' * 60}\n")

    try:
        result = preprocessor.process(args.input)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[PIPELINE ERROR] {exc}", file=sys.stderr)
        return 2

    # ── Print summary ─────────────────────────────────────────────────── #
    print(f"\n{'═' * 60}")
    print("  PREPROCESSING COMPLETE")
    print(f"{'═' * 60}")
    print(f"  ✔ Preprocessed audio : {result.preprocessed_audio_path}")
    print(f"  ✔ VAD metadata       : {result.vad_metadata_path}")
    print(f"  ✔ Processing report  : {result.report_path}")
    print(f"\n  Duration     : {result.audio_metrics.duration_seconds:.1f} s")
    print(f"  SNR          : {result.audio_metrics.snr_db:.1f} dB")
    print(f"  RMS          : {result.audio_metrics.rms_db:.1f} dBFS")
    print(f"  Flatness     : {result.audio_metrics.spectral_flatness:.3f}")
    print(f"  Clipping     : {result.audio_metrics.clipping_ratio * 100:.3f}%")
    print(f"\n  Denoised     : {'Yes (' + result.denoise_decision.strength + ')' if result.denoise_applied else 'No'}")
    print(f"  Reason       : {result.denoise_decision.reason}")
    print(f"\n  VAD segments : {len(result.vad_result.segments)}")
    print(f"  Speech time  : {result.vad_result.total_speech_duration:.1f} s")
    print(f"  Speech ratio : {result.vad_result.speech_ratio * 100:.1f}%")
    print(f"\n  Processing   : {result.processing_time_seconds:.2f} s")
    print(f"{'═' * 60}\n")

    # Print first 3 VAD segments for quick inspection.
    if result.vad_result.segments:
        print("  VAD segment preview (first 3):")
        for i, seg in enumerate(result.vad_result.segments[:3]):
            print(f"    [{i + 1}] {seg.start:.1f}s → {seg.end:.1f}s  ({seg.duration:.1f}s)")
        if len(result.vad_result.segments) > 3:
            print(f"    … and {len(result.vad_result.segments) - 3} more segments")
        print()

    # ── Whisper integration tip ───────────────────────────────────────── #
    print("  Downstream Whisper integration example:")
    print("  ─" * 30)
    print("    import whisper")
    print(f'    model = whisper.load_model("base")')
    print(f'    audio_path = "{result.preprocessed_audio_path}"')
    print(f'    vad_path   = "{result.vad_metadata_path}"')
    print()
    print("    import json")
    print("    with open(vad_path) as f:")
    print("        segments = json.load(f)['speech_segments']")
    print()
    print("    for seg in segments:")
    print("        result = model.transcribe(")
    print("            audio_path,")
    print('            clip_timestamps=[(seg["start"], seg["end"])]')
    print("        )")
    print("        print(result['text'])")
    print(f"{'─' * 60}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
