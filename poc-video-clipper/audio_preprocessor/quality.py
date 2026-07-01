"""
Audio Quality Assessment
========================
Computes a set of lightweight, interpretable audio metrics that are used
by the adaptive denoising stage to decide whether — and how strongly —
to denoise the signal.

Metrics computed:
    ┌──────────────────┬────────────────────────────────────────────────────┐
    │ Metric           │ Description                                        │
    ├──────────────────┼────────────────────────────────────────────────────┤
    │ SNR (dB)         │ Estimated via the Waveform Amplitude Distribution  │
    │                  │ Analysis (WADA) heuristic: treat the lowest-energy │
    │                  │ frames as noise and the rest as signal.            │
    │ RMS (dBFS)       │ Overall loudness relative to full scale.          │
    │ Spectral Flatness│ Wiener entropy (0 = tonal, 1 = white noise).      │
    │ Clipping Ratio   │ Fraction of samples above the clipping threshold. │
    └──────────────────┴────────────────────────────────────────────────────┘

All metrics operate on a NumPy float32 array normalised to [-1, 1], which
is how soundfile returns PCM audio.
"""

from __future__ import annotations

import logging
from typing import Tuple

import numpy as np
import soundfile as sf

from .config import QualityConfig
from .exceptions import AudioLoadError, QualityAssessmentError
from .models import AudioMetrics

logger = logging.getLogger(__name__)

# Minimum SNR floor to avoid log(0) issues.
_SNR_FLOOR_DB: float = -60.0
# Noise-estimation percentile: frames below this energy percentile are noise.
_NOISE_ENERGY_PERCENTILE: float = 10.0


class AudioQualityAssessor:
    """
    Measures audio quality characteristics required for adaptive denoising.

    Parameters:
        config: Quality-assessment configuration.
    """

    def __init__(self, config: QualityConfig) -> None:
        self._cfg = config

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def assess(self, wav_path: str) -> AudioMetrics:
        """
        Load *wav_path* and compute all quality metrics.

        Parameters:
            wav_path: Path to a WAV file (ideally the canonical 16 kHz mono
                      output produced by :class:`~audio_preprocessor.extractor.FFmpegExtractor`).

        Returns:
            :class:`~audio_preprocessor.models.AudioMetrics` populated with
            all computed measurements.

        Raises:
            AudioLoadError: If the WAV file cannot be opened or decoded.
            QualityAssessmentError: If metric computation fails unexpectedly.
        """
        logger.info("Assessing audio quality: '%s'", wav_path)
        samples, sample_rate, num_channels = self._load(wav_path)

        try:
            snr_db = self._estimate_snr(samples, sample_rate)
            rms_db = self._compute_rms_db(samples)
            spectral_flatness = self._compute_spectral_flatness(samples)
            clipping_ratio = self._compute_clipping_ratio(samples)
        except Exception as exc:  # pragma: no cover
            raise QualityAssessmentError(
                f"Failed to compute audio metrics for '{wav_path}': {exc}"
            ) from exc

        duration = len(samples) / sample_rate

        metrics = AudioMetrics(
            snr_db=round(snr_db, 2),
            rms_db=round(rms_db, 2),
            spectral_flatness=round(float(spectral_flatness), 4),
            clipping_ratio=round(float(clipping_ratio), 6),
            duration_seconds=round(duration, 3),
            sample_rate=sample_rate,
            num_channels=num_channels,
        )

        logger.info(
            "Quality metrics — SNR: %.1f dB | RMS: %.1f dBFS | "
            "Flatness: %.3f | Clipping: %.4f%%",
            metrics.snr_db,
            metrics.rms_db,
            metrics.spectral_flatness,
            metrics.clipping_ratio * 100,
        )
        return metrics

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _load(wav_path: str) -> Tuple[np.ndarray, int, int]:
        """
        Load audio from *wav_path* into a float32 NumPy array.

        Returns:
            (samples, sample_rate, num_channels) where *samples* is always 1-D
            (mono) — multi-channel files are mixed down to mono by averaging.

        Raises:
            AudioLoadError: On file-not-found or decode failures.
        """
        try:
            data, sample_rate = sf.read(wav_path, dtype="float32", always_2d=True)
        except Exception as exc:
            raise AudioLoadError(
                f"Cannot read audio file '{wav_path}': {exc}"
            ) from exc

        num_channels = data.shape[1]
        # Mix-down to mono for scalar metric computation.
        samples: np.ndarray = data.mean(axis=1)
        return samples, int(sample_rate), num_channels

    def _estimate_snr(self, samples: np.ndarray, sample_rate: int) -> float:
        """
        Estimate SNR using a simplified frame-based energy method.

        Algorithm (simplified WADA / activity-based SNR):
            1. Divide the signal into non-overlapping frames.
            2. Compute the RMS energy of each frame.
            3. Classify the lowest-energy frames as noise (10th percentile).
            4. Classify the remaining frames as signal.
            5. SNR = 10 * log10(signal_energy / noise_energy).

        This is a *rough* estimate suitable for the adaptive denoising
        threshold — not a replacement for PESQ/STOI.
        """
        frame_len = int(sample_rate * self._cfg.snr_frame_duration_ms / 1000)
        if frame_len == 0 or len(samples) < frame_len:
            logger.warning("Audio too short for SNR estimation; returning floor.")
            return _SNR_FLOOR_DB

        # Trim to multiple of frame_len to avoid ragged last frame.
        n_frames = len(samples) // frame_len
        frames = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
        frame_rms = np.sqrt(np.mean(frames ** 2, axis=1))

        # Guard against silent audio.
        if np.max(frame_rms) < 1e-10:
            return _SNR_FLOOR_DB

        noise_threshold = np.percentile(frame_rms, _NOISE_ENERGY_PERCENTILE)
        noise_frames = frame_rms[frame_rms <= noise_threshold]
        signal_frames = frame_rms[frame_rms > noise_threshold]

        noise_energy = float(np.mean(noise_frames ** 2)) if len(noise_frames) > 0 else 1e-10
        signal_energy = float(np.mean(signal_frames ** 2)) if len(signal_frames) > 0 else 1e-10

        noise_energy = max(noise_energy, 1e-10)  # avoid log(0)
        snr = 10.0 * np.log10(signal_energy / noise_energy)
        return float(np.clip(snr, _SNR_FLOOR_DB, 100.0))

    @staticmethod
    def _compute_rms_db(samples: np.ndarray) -> float:
        """
        Compute the overall RMS energy in dBFS.

        Returns:
            RMS in decibels relative to full scale (0 dBFS = normalised 1.0).
        """
        rms = float(np.sqrt(np.mean(samples ** 2)))
        if rms < 1e-10:
            return -100.0
        return float(20.0 * np.log10(rms))

    def _compute_spectral_flatness(self, samples: np.ndarray) -> float:
        """
        Compute the Wiener entropy (spectral flatness measure).

        A value close to 0 indicates a tonal (speech-like) signal; a value
        close to 1 indicates flat-spectrum noise.

        Uses the magnitude spectrum of the full signal (one long FFT) as a
        fast approximation.  Frame-averaged computation would be more accurate
        but is not necessary for threshold-based decisions.
        """
        n_fft = self._cfg.flatness_fft_size
        # Use only the first window's worth of samples for speed on long files.
        chunk = samples[:n_fft] if len(samples) >= n_fft else samples
        spectrum = np.abs(np.fft.rfft(chunk, n=n_fft))

        # Guard against all-zero spectrum.
        spectrum = np.maximum(spectrum, 1e-10)

        geometric_mean = np.exp(np.mean(np.log(spectrum)))
        arithmetic_mean = np.mean(spectrum)

        if arithmetic_mean < 1e-10:
            return 1.0  # treat silence as maximum noise-likeness
        return float(np.clip(geometric_mean / arithmetic_mean, 0.0, 1.0))

    def _compute_clipping_ratio(self, samples: np.ndarray) -> float:
        """
        Compute the fraction of samples that exceed the clipping threshold.

        Parameters:
            samples: Normalised float32 array (values in [-1, 1]).

        Returns:
            A value in [0, 1] representing the clipped fraction.
        """
        threshold = self._cfg.clipping_threshold
        clipped = np.sum(np.abs(samples) >= threshold)
        return float(clipped / max(len(samples), 1))
