"""
Whisper Transcription Package
==============================
VAD-guided, OOM-safe MLX Whisper transcription pipeline.
"""

from .models import TranscriptSegment, TranscriptWord
from .config import WhisperConfig
from .transcriber import WhisperTranscriber

__version__ = "3.0.0-whisper"
__all__ = [
    "TranscriptSegment",
    "TranscriptWord",
    "WhisperConfig",
    "WhisperTranscriber",
]