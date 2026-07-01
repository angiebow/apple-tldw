"""
Whisper Transcription Package
==============================
Single-pass MLX Whisper transcription with post-hoc hallucination
sanitization.
"""

from .models import TranscriptSegment, TranscriptWord
from .config import WhisperConfig
from .transcriber import WhisperTranscriber
from .sanitize import TranscriptSanitizer

__version__ = "4.0.0-whisper"
__all__ = [
    "TranscriptSegment",
    "TranscriptWord",
    "WhisperConfig",
    "WhisperTranscriber",
    "TranscriptSanitizer",
]