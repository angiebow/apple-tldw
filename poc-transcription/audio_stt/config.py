"""
Transcription Configuration
============================
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WhisperConfig:
    """
    Configuration for MLX Whisper transcription.

    Attributes:
        model: MLX-community Hugging Face repo for the Whisper model.
        sample_rate: Target sample rate for the input audio (Whisper expects 16kHz).
        language: Language code, or None to let Whisper auto-detect.
        sanitize_min_duration: Segments shorter than this (seconds) are dropped
            as hallucination artifacts.
        sanitize_max_segments_per_second: Sliding-window density cap — more
            segments than this in any 1s window indicates decoder collapse.
        sanitize_duplicate_timestamp_threshold: Number of consecutive segments
            sharing an identical start timestamp before they're treated as a
            decoder stall rather than real rapid-fire speech.
    """
    model: str = "mlx-community/whisper-large-v3-turbo"
    sample_rate: int = 16000
    language: str | None = "en"
    sanitize_min_duration: float = 0.01
    sanitize_max_segments_per_second: float = 8.0
    sanitize_duplicate_timestamp_threshold: int = 5