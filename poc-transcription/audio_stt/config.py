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
        max_chunk_duration: Max seconds of audio fed to the model in one call.
            VAD segments longer than this get split at the next-best VAD
            boundary; shorter ones get merged together up to this limit.
        max_merge_gap: Max silence gap (seconds) allowed between two VAD
            segments for them to be merged into the same chunk.
        sample_rate: Target sample rate for the input audio (Whisper expects 16kHz).
    """
    model: str = "mlx-community/whisper-large-v3-turbo"
    max_chunk_duration: float = 45.0
    max_merge_gap: float = 2.5
    sample_rate: int = 16000
    language: str | None = "en"