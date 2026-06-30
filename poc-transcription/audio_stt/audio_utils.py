"""
Audio Utilities
================
Loading preprocessed audio. VAD-based chunking has been removed — MLX
Whisper's single-pass long-form decoding handles segment boundaries
internally and produces better timestamps than manually pre-sliced chunks.
"""

from typing import Tuple

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
        waveform = waveform.mean(axis=1)
    if sample_rate != expected_sample_rate:
        raise ValueError(
            f"Audio sample rate is {sample_rate} Hz, expected {expected_sample_rate} Hz. "
            "Re-run preprocessing — this module does not resample."
        )
    return waveform, sample_rate