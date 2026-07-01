"""
Denoiser Module
===============
Extensible denoising architecture following the Strategy pattern.

Class hierarchy:
    BaseDenoiser          – abstract contract every backend must fulfil
    DeepFilterNetDenoiser – concrete implementation using DeepFilterNet
    NullDenoiser          – no-op pass-through (used when denoise=False)

The :class:`DenoiserFactory` instantiates the correct backend based on
the :class:`~audio_preprocessor.config.DenoiseConfig` and the adaptive
decision recorded in :class:`~audio_preprocessor.models.DenoiseDecision`.

Design rationale:
    ┌─────────────────────────────────────────────────────────────────┐
    │  Adding a new denoiser backend only requires:                   │
    │    1. Subclassing BaseDenoiser                                  │
    │    2. Implementing the `denoise()` method                       │
    │    3. Registering the class in DenoiserFactory                  │
    └─────────────────────────────────────────────────────────────────┘

DeepFilterNet notes:
    - Requires the ``deepfilterlib`` package (see requirements.txt).
    - The model is downloaded on first use and cached locally.
    - The ``atten_lim_db`` parameter controls aggressiveness:
        mild   → 30 dB attenuation limit (preserves speech quality)
        strong → 100 dB (maximum suppression)
"""

from __future__ import annotations

import abc
import logging
import shutil
from pathlib import Path

from .config import DenoiseConfig, DenoiseStrength
from .exceptions import DenoiserError
from .models import DenoiseDecision

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────── #
# Abstract base                                                                #
# ─────────────────────────────────────────────────────────────────────────── #


class BaseDenoiser(abc.ABC):
    """
    Abstract contract for all denoiser backends.

    Concrete subclasses must override :meth:`denoise` and
    :meth:`is_available`.
    """

    @abc.abstractmethod
    def denoise(self, input_path: str, output_path: str, strength: DenoiseStrength) -> str:
        """
        Denoise the audio at *input_path* and write the result to *output_path*.

        Parameters:
            input_path: Path to the source WAV file (canonical format from FFmpeg).
            output_path: Destination path for the denoised WAV file.
            strength: Denoising intensity (``MILD`` or ``STRONG``).

        Returns:
            Absolute path to the denoised file (may equal *output_path*).

        Raises:
            DenoiserError: On any unrecoverable denoiser failure.
        """

    @property
    @abc.abstractmethod
    def is_available(self) -> bool:
        """Return ``True`` if all required dependencies are installed."""

    @property
    def name(self) -> str:
        """Human-readable name of this denoiser backend."""
        return self.__class__.__name__


# ─────────────────────────────────────────────────────────────────────────── #
# No-op pass-through                                                           #
# ─────────────────────────────────────────────────────────────────────────── #


class NullDenoiser(BaseDenoiser):
    """
    Pass-through denoiser used when adaptive decision is ``should_denoise=False``.

    Simply copies the source file to the destination path so that the rest of
    the pipeline can treat the output path as a contract regardless of whether
    denoising was applied.
    """

    @property
    def is_available(self) -> bool:
        return True

    def denoise(self, input_path: str, output_path: str, strength: DenoiseStrength) -> str:
        """Copy *input_path* to *output_path* without modification."""
        logger.info("NullDenoiser: passing through '%s'", input_path)
        shutil.copy2(input_path, output_path)
        return output_path


# ─────────────────────────────────────────────────────────────────────────── #
# DeepFilterNet                                                                #
# ─────────────────────────────────────────────────────────────────────────── #


class DeepFilterNetDenoiser(BaseDenoiser):
    """
    Denoiser backend powered by the `DeepFilterNet <https://github.com/Rikorose/DeepFilterNet>`_
    neural speech enhancement model.

    This implementation uses the ``deepfilterlib`` Python API (DF framework)
    that ships alongside the ``deepfilterlib`` PyPI package.  On first
    instantiation the model weights (~20 MB) are downloaded and cached by
    the library itself.

    Parameters:
        config: DenoiseConfig containing threshold and post-filter settings.

    Raises:
        DenoiserError: If the package is not installed or model load fails.
    """

    def __init__(self, config: DenoiseConfig) -> None:
        self._cfg = config
        self._model = None
        self._df_state = None
        self._available: bool = self._try_load_model()

    # ------------------------------------------------------------------ #
    # BaseDenoiser contract                                                #
    # ------------------------------------------------------------------ #

    @property
    def is_available(self) -> bool:
        return self._available

    def denoise(self, input_path: str, output_path: str, strength: DenoiseStrength) -> str:
        """
        Apply DeepFilterNet noise suppression.

        Parameters:
            input_path: Path to the source canonical WAV file.
            output_path: Destination path for the enhanced WAV.
            strength: Controls the ``atten_lim_db`` parameter passed to the
                      DeepFilterNet enhancer.

        Returns:
            Absolute path to the denoised file.

        Raises:
            DenoiserError: If the model is unavailable or inference fails.
        """
        if not self._available:
            raise DenoiserError(
                "DeepFilterNet is not available. "
                "Install it with: pip install deepfilterlib"
            )

        atten_lim = self._resolve_atten_lim(strength)
        logger.info(
            "DeepFilterNet denoising: strength=%s, atten_lim=%.0f dB | '%s' → '%s'",
            strength.value,
            atten_lim,
            input_path,
            output_path,
        )

        try:
            self._run_inference(input_path, output_path, atten_lim)
        except Exception as exc:
            raise DenoiserError(f"DeepFilterNet inference failed: {exc}") from exc

        return output_path

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _try_load_model(self) -> bool:
        """
        Attempt to import DeepFilterNet and load the model.

        Returns ``False`` (rather than raising) so that the orchestrator
        can gracefully fall back to the NullDenoiser with a warning.
        """
        try:
            # Lazy import to avoid hard dependency at import time.
            from df.enhance import init_df  # type: ignore[import]

            logger.debug("Loading DeepFilterNet model…")
            self._model, self._df_state, _ = init_df(
                post_filter=self._cfg.deepfilter_post_filter
            )
            logger.info("DeepFilterNet model loaded successfully.")
            return True
        except ImportError:
            logger.warning(
                "deepfilterlib not installed. "
                "Install with: pip install deepfilterlib"
            )
            return False
        except Exception as exc:  # pragma: no cover
            logger.warning("DeepFilterNet model load failed: %s", exc)
            return False

    def _resolve_atten_lim(self, strength: DenoiseStrength) -> float:
        """Map a :class:`DenoiseStrength` to the corresponding attenuation limit."""
        mapping = {
            DenoiseStrength.MILD: self._cfg.mild_atten_lim_db,
            DenoiseStrength.STRONG: self._cfg.strong_atten_lim_db,
        }
        return mapping.get(strength, self._cfg.strong_atten_lim_db)

    def _run_inference(
        self, input_path: str, output_path: str, atten_lim_db: float
    ) -> None:
        """
        Execute the DeepFilterNet inference pass.

        Loads the source audio using soundfile (via ``df.io``), runs the
        enhancer, and writes the result.  The ``atten_lim_db`` is applied
        per-frame so that speech harmonics are preserved at the configured
        level.
        """
        import torch  # type: ignore[import]
        from df.enhance import enhance, load_audio, save_audio  # type: ignore[import]

        # load_audio returns a (C, T) float32 tensor at the model's sample rate.
        audio, _ = load_audio(input_path, sr=self._df_state.sr())
        enhanced = enhance(
            self._model,
            self._df_state,
            audio,
            atten_lim_db=atten_lim_db,
        )
        save_audio(output_path, enhanced, self._df_state.sr())


# ─────────────────────────────────────────────────────────────────────────── #
# Factory                                                                      #
# ─────────────────────────────────────────────────────────────────────────── #


class DenoiserFactory:
    """
    Instantiates and returns the appropriate :class:`BaseDenoiser` backend.

    Resolution order:
        1. If ``DeepFilterNetDenoiser`` is available → use it.
        2. Otherwise fall back to ``NullDenoiser`` with a warning.

    This ensures the pipeline never crashes because DeepFilterNet is not
    installed — it degrades gracefully.
    """

    @staticmethod
    def create(config: DenoiseConfig) -> BaseDenoiser:
        """
        Create a denoiser backend from *config*.

        Parameters:
            config: The denoising configuration.

        Returns:
            An instantiated, ready-to-use :class:`BaseDenoiser`.
        """
        candidate = DeepFilterNetDenoiser(config)
        if candidate.is_available:
            return candidate

        logger.warning(
            "DeepFilterNet unavailable; falling back to NullDenoiser. "
            "Install deepfilterlib for real noise suppression."
        )
        return NullDenoiser()


# ─────────────────────────────────────────────────────────────────────────── #
# Adaptive decision logic (kept here to be co-located with the denoiser)      #
# ─────────────────────────────────────────────────────────────────────────── #


def decide_denoising(metrics: "AudioMetrics", config: DenoiseConfig) -> DenoiseDecision:  # noqa: F821
    """
    Composite adaptive denoising decision using all four quality metrics.

    Why four metrics instead of just SNR?
        * SNR alone can be fooled: a signal with good SNR but high spectral
          flatness (e.g. crowd noise or background music) still needs denoising.
        * Spectral Flatness catches cases where noise blends into the spectrum
          and is invisible to simple SNR estimation.
        * Clipping is distortion, NOT noise — denoising cannot repair it, but
          the caller must be warned.
        * RMS is contextualised in the reason string (handled by normaliser).

    Scoring algorithm:
        SNR score  : 0 (clean) | 1 (mild) | 2 (heavy)
        Flat score : 0 (tonal) | 1 (borderline) | 2 (noise-like)
        composite  = max(snr_score, flat_score)
                     escalated +1 if BOTH >= 1  (belt-and-suspenders)
        0 -> NONE  |  1 -> MILD  |  2 -> STRONG

    Parameters:
        metrics: :class:`~audio_preprocessor.models.AudioMetrics` produced
                 by the quality assessment stage.
        config:  Denoising configuration containing all thresholds.

    Returns:
        :class:`~audio_preprocessor.models.DenoiseDecision` with a detailed
        multi-metric reason string.
    """
    # Import here to avoid circular import (models imports nothing from denoiser).
    snr_db = metrics.snr_db
    flatness = metrics.spectral_flatness
    clipping = metrics.clipping_ratio

    # ── Forced override ────────────────────────────────────────────────
    if config.force_denoise:
        return DenoiseDecision(
            should_denoise=True,
            strength=DenoiseStrength.STRONG.value,
            reason=(
                f"Forced denoising via config "
                f"(SNR={snr_db:.1f} dB, flatness={flatness:.3f}, force_denoise=True)."
            ),
            snr_at_decision=snr_db,
        )

    # ── SNR score ──────────────────────────────────────────────────────
    if snr_db >= config.snr_clean_threshold:
        snr_score, snr_label = 0, f"clean SNR ({snr_db:.1f} dB >= {config.snr_clean_threshold:.0f} dB)"
    elif snr_db >= config.snr_mild_threshold:
        snr_score, snr_label = 1, f"moderate noise (SNR {snr_db:.1f} dB)"
    else:
        snr_score, snr_label = 2, f"heavy noise (SNR {snr_db:.1f} dB < {config.snr_mild_threshold:.0f} dB)"

    # ── Spectral Flatness score ────────────────────────────────────────
    # Tonal speech  : 0.0 – flatness_clean_threshold  → score 0 (clean)
    # Background mix: flatness_clean – flatness_noisy  → score 1 (borderline)
    # Broadband noise: flatness_noisy – 1.0            → score 2 (noise-like)
    if flatness < config.flatness_clean_threshold:
        flat_score, flat_label = 0, f"tonal spectrum (flatness={flatness:.3f})"
    elif flatness < config.flatness_noisy_threshold:
        flat_score, flat_label = 1, f"borderline spectrum (flatness={flatness:.3f})"
    else:
        flat_score, flat_label = 2, (
            f"noise-like spectrum (flatness={flatness:.3f} "
            f">= {config.flatness_noisy_threshold:.2f})"
        )

    # ── Composite score ────────────────────────────────────────────────
    composite = max(snr_score, flat_score)
    if snr_score >= 1 and flat_score >= 1:
        composite = min(composite + 1, 2)   # both bad -> escalate

    # ── Clipping warning ───────────────────────────────────────────────
    clipping_note = ""
    if clipping > config.clipping_warn_threshold:
        clipping_note = (
            f" | CLIPPING WARNING: {clipping*100:.3f}% of samples are clipped "
            "(distortion — denoising will NOT repair this)."
        )
        logger.warning(
            "Clipping ratio %.4f > threshold %.4f. "
            "Audio may contain permanent distortion.",
            clipping, config.clipping_warn_threshold,
        )

    # ── Final decision ─────────────────────────────────────────────────
    if composite == 0:
        return DenoiseDecision(
            should_denoise=False,
            strength=DenoiseStrength.NONE.value,
            reason=f"Audio is clean: {snr_label}; {flat_label}.{clipping_note}",
            snr_at_decision=snr_db,
        )
    if composite == 1:
        return DenoiseDecision(
            should_denoise=True,
            strength=DenoiseStrength.MILD.value,
            reason=(
                f"Mild noise: {snr_label}; {flat_label}. "
                f"Applying mild denoising.{clipping_note}"
            ),
            snr_at_decision=snr_db,
        )
    return DenoiseDecision(
        should_denoise=True,
        strength=DenoiseStrength.STRONG.value,
        reason=(
            f"Significant noise: {snr_label}; {flat_label}. "
            f"Applying strong denoising.{clipping_note}"
        ),
        snr_at_decision=snr_db,
    )
