"""
Data Models
===========
Immutable dataclasses that represent the structured outputs produced by each
stage of the preprocessing pipeline.  These act as the contracts between
pipeline stages and are safe to serialise to JSON.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Stage 1 – Audio quality metrics                                             #
# --------------------------------------------------------------------------- #


@dataclass
class AudioMetrics:
    """
    Snapshot of audio-quality measurements used for adaptive decision making.

    All values are computed on the *extracted* (pre-denoising) audio so that
    the denoising decision can be made before any destructive processing.

    Attributes:
        snr_db: Estimated Signal-to-Noise Ratio in decibels.
        rms_db: Root-mean-square energy in decibels (relative to full scale).
        spectral_flatness: Wiener entropy (0 = tonal, 1 = flat/noise-like).
        clipping_ratio: Fraction of samples above the clipping threshold.
        duration_seconds: Total audio duration in seconds.
        sample_rate: Sample rate of the analysed audio (Hz).
        num_channels: Number of audio channels analysed.
    """

    snr_db: float
    rms_db: float
    spectral_flatness: float
    clipping_ratio: float
    duration_seconds: float
    sample_rate: int
    num_channels: int

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return asdict(self)


# --------------------------------------------------------------------------- #
# Stage 2 – Adaptive denoising decision                                       #
# --------------------------------------------------------------------------- #


@dataclass
class DenoiseDecision:
    """
    Structured record of the adaptive denoising decision.

    Attributes:
        should_denoise: Whether denoising was applied.
        strength: Denoising intensity level selected (``"none"`` / ``"mild"`` /
                  ``"strong"``).
        reason: Human-readable explanation of why this decision was made.
        snr_at_decision: The SNR value that triggered the decision.
    """

    should_denoise: bool
    strength: str  # DenoiseStrength value
    reason: str
    snr_at_decision: float

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return asdict(self)


# --------------------------------------------------------------------------- #
# Stage 3 – Loudness normalisation result                                     #
# --------------------------------------------------------------------------- #


@dataclass
class NormalizationResult:
    """
    Record of the loudness normalisation step.

    Attributes:
        input_lufs: Integrated loudness of the audio *before* normalisation (LUFS).
        output_lufs: Achieved integrated loudness *after* normalisation (LUFS).
        target_lufs: The configured target loudness (LUFS).
        gain_db: Linear gain applied in decibels.
        clipping_prevented: ``True`` if the clipping guard reduced the gain
                            below what was needed to hit ``target_lufs``.
        normalization_applied: ``False`` if normalisation was skipped
                               (e.g. silent audio or ``enabled=False``).
    """

    input_lufs: float
    output_lufs: float
    target_lufs: float
    gain_db: float
    clipping_prevented: bool
    normalization_applied: bool

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return asdict(self)


# --------------------------------------------------------------------------- #
# Stage 4 – Voice activity detection                                          #
# --------------------------------------------------------------------------- #


@dataclass
class VADSegment:
    """
    A single detected speech segment.

    Attributes:
        start: Segment start time in seconds (relative to original timeline).
        end: Segment end time in seconds (relative to original timeline).
    """

    start: float
    end: float

    @property
    def duration(self) -> float:
        """Segment duration in seconds."""
        return self.end - self.start

    def to_dict(self) -> Dict[str, float]:
        """Return a JSON-serialisable dictionary."""
        return {"start": round(self.start, 3), "end": round(self.end, 3)}


@dataclass
class VADResult:
    """
    Aggregated result from the VAD stage.

    Attributes:
        segments: List of detected speech segments.
        total_speech_duration: Sum of all segment durations in seconds.
        speech_ratio: Fraction of total audio that contains speech.
    """

    segments: List[VADSegment]
    total_speech_duration: float
    speech_ratio: float

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return {
            "speech_segments": [s.to_dict() for s in self.segments],
            "total_speech_duration_seconds": round(self.total_speech_duration, 3),
            "speech_ratio": round(self.speech_ratio, 4),
        }


# --------------------------------------------------------------------------- #
# Final pipeline result                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class PreprocessingResult:
    """
    Container for all outputs produced by :class:`~audio_preprocessor.AudioPreprocessor`.

    Attributes:
        preprocessed_audio_path: Absolute path to the exported preprocessed WAV.
        vad_metadata_path: Absolute path to the exported VAD JSON file.
        report_path: Absolute path to the exported processing-report JSON file.
        audio_metrics: Quality metrics recorded before denoising.
        denoise_decision: Record of whether and how denoising was applied.
        normalization_result: Loudness normalisation metrics and applied gain.
        vad_result: Voice activity detection output.
        processing_time_seconds: Wall-clock time for the full pipeline run.
        input_path: Original input file path.
        denoise_applied: Convenience alias derived from ``denoise_decision``.
    """

    preprocessed_audio_path: str
    vad_metadata_path: str
    report_path: str
    audio_metrics: AudioMetrics
    denoise_decision: DenoiseDecision
    normalization_result: NormalizationResult
    vad_result: VADResult
    processing_time_seconds: float
    input_path: str
    denoise_executed: bool = False   # True only if a real denoiser ran (not NullDenoiser)
    denoiser_backend: str = "NullDenoiser"  # name of the backend that ran
    agc_applied: bool = False

    @property
    def denoise_applied(self) -> bool:
        """True if denoising was BOTH decided AND actually executed by a real backend."""
        return self.denoise_decision.should_denoise and self.denoise_executed

    def to_report_dict(self) -> Dict[str, Any]:
        """
        Return the processing report as a JSON-serialisable dictionary.

        This is what gets written to *processing_report.json*.
        """
        return {
            "input_path": self.input_path,
            "preprocessed_audio_path": self.preprocessed_audio_path,
            "audio_metrics": self.audio_metrics.to_dict(),
            "denoising": {
                "decision": self.denoise_decision.should_denoise,
                "executed": self.denoise_executed,
                "strength": self.denoise_decision.strength,
                "backend": self.denoiser_backend,
                "reason": self.denoise_decision.reason,
            },
            "agc": {
                "applied": self.agc_applied,
            },
            "normalization": self.normalization_result.to_dict(),
            "vad_summary": {
                "num_segments": len(self.vad_result.segments),
                "total_speech_duration_seconds": self.vad_result.total_speech_duration,
                "speech_ratio": self.vad_result.speech_ratio,
            },
            "processing_time_seconds": round(self.processing_time_seconds, 3),
        }

    def to_vad_dict(self) -> Dict[str, Any]:
        """
        Return the VAD metadata as a JSON-serialisable dictionary.

        This is what gets written to *vad_metadata.json*.
        """
        return self.vad_result.to_dict()

    def __str__(self) -> str:  # pragma: no cover
        return json.dumps(self.to_report_dict(), indent=2)
