"""
Configuration Module
====================
Centralised, dataclass-based configuration for every stage of the
audio preprocessing pipeline.  All thresholds and paths are set here –
nothing is hardcoded elsewhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DenoiseStrength(str, Enum):
    """Denoising intensity levels understood by the denoiser backend."""

    NONE = "none"
    MILD = "mild"
    STRONG = "strong"


class AudioFormat(str, Enum):
    """Canonical output audio format."""

    WAV = "wav"


@dataclass
class FFmpegConfig:
    """
    FFmpeg extraction settings.

    Attributes:
        sample_rate: Target sample rate in Hz (default 16 000, optimal for STT).
        channels: Number of audio channels (1 = mono).
        codec: PCM codec used for WAV output.
        overwrite: Whether to overwrite existing output files.
        ffmpeg_path: Explicit path to the ffmpeg binary.
                     If ``None``, the binary is resolved from ``$PATH``.
    """

    sample_rate: int = 16_000
    channels: int = 1
    codec: str = "pcm_s16le"
    overwrite: bool = True
    ffmpeg_path: str = "ffmpeg"


@dataclass
class QualityConfig:
    """
    Audio quality assessment parameters.

    Attributes:
        snr_frame_duration_ms: Frame duration (ms) used when estimating SNR.
        flatness_fft_size: FFT window size for spectral-flatness computation.
        clipping_threshold: Absolute normalised amplitude considered clipped (0–1).
    """

    snr_frame_duration_ms: int = 30
    flatness_fft_size: int = 2048
    clipping_threshold: float = 0.99


@dataclass
class DenoiseConfig:
    """
    Adaptive denoising decision thresholds and backend settings.

    SNR decision logic:
        SNR >= snr_clean_threshold  -> no denoising
        snr_mild_threshold <= SNR < snr_clean_threshold -> mild
        SNR < snr_mild_threshold    -> strong

    Spectral Flatness decision logic (tonal=0.0, noise=1.0):
        flatness < flatness_clean_threshold  -> score 0 (tonal / speech-like)
        flatness < flatness_noisy_threshold  -> score 1 (borderline)
        flatness >= flatness_noisy_threshold -> score 2 (noise-like)

    Attributes:
        snr_clean_threshold: SNR (dB) above which audio is considered clean.
        snr_mild_threshold: SNR (dB) above which mild denoising suffices.
        flatness_clean_threshold: Flatness below which audio is tonal (clean).
        flatness_noisy_threshold: Flatness above which audio is noise-like.
        clipping_warn_threshold: Clipping ratio above which a warning is logged.
        deepfilter_post_filter: Enable post-filter in DeepFilterNet.
        mild_atten_lim_db: Attenuation limit (dB) in *mild* mode.
        strong_atten_lim_db: Attenuation limit (dB) in *strong* mode.
        force_denoise: Force denoising regardless of quality assessment.
    """

    snr_clean_threshold: float = 20.0
    snr_mild_threshold: float = 10.0
    flatness_clean_threshold: float = 0.3    # below → tonal (clean speech)
    flatness_noisy_threshold: float = 0.6    # above → noise-like spectrum
    clipping_warn_threshold: float = 0.005   # warn if > 0.5% samples clipped
    deepfilter_post_filter: bool = True
    mild_atten_lim_db: float = 30.0
    strong_atten_lim_db: float = 100.0
    force_denoise: bool = False


@dataclass
class VADConfig:
    """
    Silero VAD tuning parameters.

    Attributes:
        threshold: Speech probability threshold (0–1). Lower → more sensitive.
        min_speech_duration_ms: Minimum duration (ms) to count as a speech segment.
        max_speech_duration_s: Hard ceiling (s) per segment; longer segments are
                               split at silence boundaries.
        min_silence_duration_ms: Minimum silence gap (ms) that terminates a segment.
        window_size_samples: Silero model inference window.  Must be 512, 1024, or
                             1536 for the 16 kHz model.
        speech_pad_ms: Padding (ms) added around each detected speech segment.
    """

    threshold: float = 0.5
    min_speech_duration_ms: int = 250
    max_speech_duration_s: float = 3600.0  # effectively unlimited per segment
    min_silence_duration_ms: int = 500
    window_size_samples: int = 1024
    speech_pad_ms: int = 30


@dataclass
class AGCConfig:
    """
    Automatic Gain Control / Dynamic Range Compression settings.
    Uses FFmpeg's `dynaudnorm` (Dynamic Audio Normalizer) filter.

    Attributes:
        enabled: Toggle AGC on/off.
        frame_len_ms: Frame length in milliseconds (default 150).
        filter_size: Gaussian filter window size (default 15).
        max_gain: Maximum gain factor (default 10.0).
        target_peak: Target peak level (default 0.95).
    """

    enabled: bool = True
    frame_len_ms: int = 150
    filter_size: int = 15
    max_gain: float = 10.0
    target_peak: float = 0.95


@dataclass
class NormalizationConfig:
    """
    Loudness normalisation settings (EBU R128 / ITU-R BS.1770).

    Normalisation is applied **after denoising and before VAD** so that:
        1. True loudness is measured on a clean signal (noise inflates LUFS).
        2. Silero VAD receives audio at a consistent loudness level.
        3. The STT model (e.g. Whisper) receives broadcast-standard input.

    Attributes:
        enabled: Toggle normalisation on/off without changing other settings.
        target_lufs: Target integrated loudness in LUFS.
                     -23 LUFS = EBU R128 broadcast standard.
                     -16 LUFS = common podcast / streaming target.
        max_peak: Maximum allowable sample peak after normalisation (0–1).
                  Prevents digital clipping; the actual gain is reduced if
                  normalisation would exceed this value.
        sample_rate: Sample rate of the audio being normalised.  Should match
                     ``FFmpegConfig.sample_rate``.
    """

    enabled: bool = True
    target_lufs: float = -23.0   # EBU R128 broadcast standard
    max_peak: float = 0.99       # clipping guard
    sample_rate: int = 16_000


@dataclass
class PreprocessorConfig:
    """
    Top-level configuration object passed to :class:`AudioPreprocessor`.

    Attributes:
        output_dir: Directory where all output artefacts are written.
        ffmpeg: FFmpeg extraction settings.
        quality: Audio quality assessment settings.
        denoise: Adaptive denoising decision settings.
        normalize: Loudness normalisation settings (EBU R128).
        vad: Silero VAD settings.
        log_level: Python logging level (e.g. ``"INFO"``, ``"DEBUG"``).
        keep_intermediate: Retain intermediate WAV files created during
                           processing (useful for debugging).
        preprocessed_filename: Base name of the exported preprocessed audio.
        vad_metadata_filename: Base name of the exported VAD JSON file.
        report_filename: Base name of the exported processing-report JSON file.
    """

    output_dir: str = "output"
    ffmpeg: FFmpegConfig = field(default_factory=FFmpegConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    denoise: DenoiseConfig = field(default_factory=DenoiseConfig)
    normalize: NormalizationConfig = field(default_factory=NormalizationConfig)
    agc: AGCConfig = field(default_factory=AGCConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    log_level: str = "INFO"
    keep_intermediate: bool = False
    preprocessed_filename: str = "preprocessed.wav"
    vad_metadata_filename: str = "vad_metadata.json"
    report_filename: str = "processing_report.json"

    # ------------------------------------------------------------------ #
    # Convenience helpers                                                  #
    # ------------------------------------------------------------------ #

    @property
    def output_path(self) -> str:
        """Absolute path to the configured output directory."""
        return os.path.abspath(self.output_dir)

    def resolve_output(self, filename: str) -> str:
        """Join *filename* with the resolved output directory."""
        return os.path.join(self.output_path, filename)
