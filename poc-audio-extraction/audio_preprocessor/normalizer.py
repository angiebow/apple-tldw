"""
Loudness Normalizer
===================
Implements EBU R128 / ITU-R BS.1770 integrated loudness normalisation using
the ``pyloudnorm`` library.

Why EBU R128 (-23 LUFS)?
    • Industry standard for broadcast, streaming, and podcast delivery.
    • Whisper / STT models trained on normalised audio perform more
      consistently when the input loudness is predictable.
    • Silero VAD detection thresholds are implicitly calibrated for audio
      in a reasonable loudness range — extreme quiet or loud recordings
      can shift detection accuracy.

Pipeline position: **after denoising, before VAD**.

    Denoise first → measure true loudness (noise inflates LUFS readings)
    Normalise     → consistent input for VAD and STT
    VAD           → most accurate segment detection
    Write output  → Whisper receives broadcast-standard audio

Clipping guard:
    If normalisation would push any sample above ±1.0, the gain is reduced
    to the maximum safe level and a warning is logged.  The actual achieved
    loudness is always recorded so downstream systems know what they received.
"""

from __future__ import annotations

import logging

import numpy as np
import soundfile as sf

from .config import NormalizationConfig
from .exceptions import AudioPreprocessorError
from .models import NormalizationResult

logger = logging.getLogger(__name__)


class NormalizationError(AudioPreprocessorError):
    """Raised when loudness normalisation fails."""


class LoudnessNormalizer:
    """
    Normalises audio to a target integrated loudness using pyloudnorm.

    Parameters:
        config: :class:`~audio_preprocessor.config.NormalizationConfig`
                with target LUFS and clipping guard settings.

    Raises:
        NormalizationError: If pyloudnorm is not installed or processing fails.
    """

    def __init__(self, config: NormalizationConfig) -> None:
        self._cfg = config
        self._meter = self._build_meter()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def normalize(self, input_path: str, output_path: str) -> NormalizationResult:
        """
        Normalise the WAV file at *input_path* and write the result to
        *output_path*.

        Parameters:
            input_path: Path to the denoised WAV (16 kHz, mono, float32).
            output_path: Destination path for the normalised WAV.

        Returns:
            :class:`~audio_preprocessor.models.NormalizationResult` recording
            the measured loudness before and after normalisation, and the
            linear gain applied.

        Raises:
            NormalizationError: On any processing failure.
        """
        logger.info("Normalising loudness: '%s' → '%s'", input_path, output_path)

        samples, sample_rate = self._load(input_path)

        # ── Measure input loudness ─────────────────────────────────────
        input_lufs = self._measure_lufs(samples, sample_rate)
        logger.info("Input loudness: %.2f LUFS", input_lufs)

        if not np.isfinite(input_lufs):
            # Audio is effectively silent — skip normalisation.
            logger.warning(
                "Audio appears silent (LUFS=%.2f); skipping normalisation.",
                input_lufs,
            )
            sf.write(output_path, samples, sample_rate, subtype="PCM_16")
            return NormalizationResult(
                input_lufs=input_lufs,
                output_lufs=input_lufs,
                target_lufs=self._cfg.target_lufs,
                gain_db=0.0,
                clipping_prevented=False,
                normalization_applied=False,
            )

        # ── Compute required gain ──────────────────────────────────────
        gain_db = self._cfg.target_lufs - input_lufs
        gain_linear = 10.0 ** (gain_db / 20.0)
        normalised = samples * gain_linear

        # ── Clipping guard ─────────────────────────────────────────────
        clipping_prevented = False
        peak = float(np.max(np.abs(normalised)))
        if peak > self._cfg.max_peak:
            safe_gain = self._cfg.max_peak / float(np.max(np.abs(samples)))
            gain_db = 20.0 * np.log10(safe_gain)
            normalised = samples * safe_gain
            clipping_prevented = True
            logger.warning(
                "Clipping guard triggered: peak=%.3f > %.3f. "
                "Gain reduced to %.2f dB (target was %.2f dB).",
                peak,
                self._cfg.max_peak,
                gain_db,
                self._cfg.target_lufs - input_lufs,
            )

        # ── Measure achieved loudness ──────────────────────────────────
        output_lufs = self._measure_lufs(normalised, sample_rate)

        # ── Write output ───────────────────────────────────────────────
        sf.write(output_path, normalised, sample_rate, subtype="PCM_16")

        result = NormalizationResult(
            input_lufs=round(input_lufs, 2),
            output_lufs=round(output_lufs, 2),
            target_lufs=self._cfg.target_lufs,
            gain_db=round(gain_db, 2),
            clipping_prevented=clipping_prevented,
            normalization_applied=True,
        )

        logger.info(
            "Normalisation complete: %.2f LUFS → %.2f LUFS (gain %.2f dB)%s",
            input_lufs,
            output_lufs,
            gain_db,
            " [clipping guard applied]" if clipping_prevented else "",
        )
        return result

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _build_meter(self):
        """
        Instantiate a pyloudnorm BS.1770 meter at the configured sample rate.

        Raises:
            NormalizationError: If pyloudnorm is not installed.
        """
        try:
            import pyloudnorm as pyln  # type: ignore[import]
            return pyln.Meter(self._cfg.sample_rate)
        except ImportError as exc:
            raise NormalizationError(
                "pyloudnorm is not installed. "
                "Install it with: pip install pyloudnorm"
            ) from exc

    @staticmethod
    def _load(path: str) -> tuple[np.ndarray, int]:
        """Load a WAV file as a float64 NumPy array (pyloudnorm requires float64)."""
        try:
            data, sample_rate = sf.read(path, dtype="float64", always_2d=True)
        except Exception as exc:
            raise NormalizationError(f"Cannot read '{path}': {exc}") from exc
        # Mix to mono for metering (mono audio stays mono).
        return data.mean(axis=1), int(sample_rate)

    def _measure_lufs(self, samples: np.ndarray, sample_rate: int) -> float:
        """
        Measure integrated loudness in LUFS (EBU R128).

        pyloudnorm expects a float64 array shaped (samples,) for mono or
        (samples, channels) for multi-channel.
        """
        try:
            # Meter was built with config sample_rate; assert match.
            if sample_rate != self._cfg.sample_rate:
                logger.warning(
                    "Sample rate mismatch: meter=%d Hz, audio=%d Hz. "
                    "Rebuilding meter.",
                    self._cfg.sample_rate,
                    sample_rate,
                )
                import pyloudnorm as pyln  # type: ignore[import]
                meter = pyln.Meter(sample_rate)
            else:
                meter = self._meter

            lufs = meter.integrated_loudness(samples)
            return float(lufs)
        except Exception as exc:  # pragma: no cover
            raise NormalizationError(f"LUFS measurement failed: {exc}") from exc
