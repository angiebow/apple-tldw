"""
Audio Preprocessor Package
==========================
Production-quality audio preprocessing pipeline for long-form podcast videos.

This package handles:
    - Audio/video extraction via FFmpeg
    - Audio quality assessment (SNR, RMS, Spectral Flatness, Clipping Ratio)
    - Adaptive denoising via DeepFilterNet
    - Voice Activity Detection via Silero VAD
    - Structured output for downstream STT pipelines

Usage:
    from audio_preprocessor import AudioPreprocessor, PreprocessorConfig

    config = PreprocessorConfig(output_dir="output/")
    preprocessor = AudioPreprocessor(config)
    result = preprocessor.process("podcast.mp4")
"""

from .preprocessor import AudioPreprocessor
from .config import PreprocessorConfig, AGCConfig
from .models import (
    AudioMetrics,
    DenoiseDecision,
    NormalizationResult,
    VADSegment,
    PreprocessingResult,
)

__all__ = [
    "AudioPreprocessor",
    "PreprocessorConfig",
    "AGCConfig",
    "AudioMetrics",
    "DenoiseDecision",
    "NormalizationResult",
    "VADSegment",
    "PreprocessingResult",
]

__version__ = "1.0.0"
__author__ = "ML Engineering Team"
