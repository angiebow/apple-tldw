"""
Audio Utilities
================
Loading preprocessed audio, loading VAD segments, and merging/splitting
those VAD segments into model-safe chunks for Whisper.
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import soundfile as sf


def load_audio(audio_path: str, expected_sample_rate: int = 16000) -> Tuple[np.ndarray, int]:
    """
    Load a preprocessed WAV file. Assumes it's already mono and resampled
    (poc-audio-extraction's preprocessing step guarantees this) — no
    resampling/normalization is done here to avoid touching the audio twice.
    """
    waveform, sample_rate = sf.read(audio_path, dtype="float32", always_2d=False)
    if waveform.ndim > 1:
        waveform = waveform.mean(axis=1)  # safety net, shouldn't trigger on preprocessed audio
    if sample_rate != expected_sample_rate:
        raise ValueError(
            f"Audio sample rate is {sample_rate} Hz, expected {expected_sample_rate} Hz. "
            "Re-run preprocessing — this module does not resample."
        )
    return waveform, sample_rate


def load_vad_segments(vad_path: str) -> List[Dict[str, float]]:
    """Load VAD speech segments from the VAD metadata JSON."""
    path = Path(vad_path)
    if not path.is_file():
        raise FileNotFoundError(f"VAD metadata file not found: {vad_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    segments = data.get("speech_segments", [])
    if not segments:
        raise ValueError("VAD metadata contains no speech_segments")
    return segments


def chunk_segments(
    waveform: np.ndarray,
    sample_rate: int,
    vad_segments: List[Dict[str, float]],
    max_chunk_duration: float = 45.0,
    max_merge_gap: float = 1.0,
) -> List[Dict]:
    """
    Turn raw VAD segments into model-safe audio chunks.

    Two problems get fixed here:
      - Short VAD segments (0.1-2s) get merged together (as long as the gap
        between them is small) so Whisper sees enough context per call
        instead of isolated word fragments.
      - Long VAD segments (60-100s+) get split into sub-chunks under
        max_chunk_duration to avoid OOM, since no internal VAD boundary
        exists to split on cleanly.

    Returns a list of dicts: {"waveform": np.ndarray, "start": float, "end": float}
    where start/end are global timestamps to offset Whisper's chunk-local output by.
    """
    sorted_segments = sorted(vad_segments, key=lambda s: s["start"])
    chunks: List[Dict] = []

    current_start = None
    current_end = None

    def flush_chunk(start: float, end: float):
        start_sample = int(start * sample_rate)
        end_sample = int(end * sample_rate)
        chunks.append({
            "waveform": waveform[start_sample:end_sample],
            "start": start,
            "end": end,
        })

    for seg in sorted_segments:
        seg_start, seg_end = seg["start"], seg["end"]
        if seg_end <= seg_start:
            continue

        # Split an overlong single segment into fixed-size sub-chunks.
        if seg_end - seg_start > max_chunk_duration:
            if current_start is not None:
                flush_chunk(current_start, current_end)
                current_start = None
            t = seg_start
            while t < seg_end:
                sub_end = min(t + max_chunk_duration, seg_end)
                flush_chunk(t, sub_end)
                t = sub_end
            continue

        if current_start is None:
            current_start, current_end = seg_start, seg_end
            continue

        gap = seg_start - current_end
        merged_duration = seg_end - current_start
        if gap <= max_merge_gap and merged_duration <= max_chunk_duration:
            current_end = seg_end
        else:
            flush_chunk(current_start, current_end)
            current_start, current_end = seg_start, seg_end

    if current_start is not None:
        flush_chunk(current_start, current_end)

    return chunks