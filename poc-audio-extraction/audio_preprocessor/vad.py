"""
Voice Activity Detection
========================
Wraps the official Silero VAD model to detect speech segments in the
preprocessed audio, returning clean metadata for downstream STT systems.

Key design decisions:
    • Timestamps are returned in **seconds** (not samples) to be agnostic
      of sample rate changes downstream.
    • The original audio timeline is preserved — segments reference the
      source file's timestamps, not chunk offsets.
    • No audio chunks are written to disk — the VAD output is purely
      metadata, letting each downstream STT provider (Whisper, Google
      Speech-to-Text, etc.) consume the full audio with timestamps.

Silero VAD details:
    • Model loaded via ``torch.hub.load("snakers4/silero-vad", "silero_vad")``.
    • The 16 kHz model requires ``window_size_samples`` ∈ {512, 1024, 1536}.
    • The ``get_speech_timestamps`` utility returns dicts of
      ``{"start": <sample>, "end": <sample>}`` which we convert to seconds.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import soundfile as sf
import torch

from .config import VADConfig
from .exceptions import AudioLoadError, VADError
from .models import VADResult, VADSegment

logger = logging.getLogger(__name__)

# Silero VAD hub coordinates.
_SILERO_REPO = "snakers4/silero-vad"
_SILERO_MODEL = "silero_vad"


class SileroVAD:
    """
    Voice Activity Detection powered by the Silero VAD model.

    The model is loaded lazily on the first call to :meth:`detect` to
    avoid import-time network access.

    Parameters:
        config: :class:`~audio_preprocessor.config.VADConfig` controlling
                thresholds and window sizes.
    """

    def __init__(self, config: VADConfig) -> None:
        self._cfg = config
        self._model: Any = None
        self._get_speech_timestamps: Any = None

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def detect(self, wav_path: str) -> VADResult:
        """
        Run VAD on the WAV file at *wav_path* and return speech segments.

        The function does **not** write any audio files — it returns only
        timestamps for downstream consumption.

        Parameters:
            wav_path: Absolute path to the (preprocessed) WAV file.

        Returns:
            :class:`~audio_preprocessor.models.VADResult` containing a list of
            :class:`~audio_preprocessor.models.VADSegment` objects with
            ``start`` and ``end`` timestamps in seconds.

        Raises:
            AudioLoadError: If the WAV file cannot be read.
            VADError: If the Silero model fails to load or run inference.
        """
        logger.info("Running Silero VAD on '%s'", wav_path)
        self._ensure_model_loaded()
        audio_tensor, sample_rate = self._load_audio(wav_path)

        try:
            raw_segments = self._get_speech_timestamps(
                audio_tensor,
                self._model,
                threshold=self._cfg.threshold,
                sampling_rate=sample_rate,
                min_speech_duration_ms=self._cfg.min_speech_duration_ms,
                max_speech_duration_s=self._cfg.max_speech_duration_s,
                min_silence_duration_ms=self._cfg.min_silence_duration_ms,
                window_size_samples=self._cfg.window_size_samples,
                speech_pad_ms=self._cfg.speech_pad_ms,
                return_seconds=False,  # we convert manually for precision
            )
        except Exception as exc:
            raise VADError(f"Silero VAD inference failed: {exc}") from exc

        segments = self._convert_segments(raw_segments, sample_rate)
        total_duration = len(audio_tensor) / sample_rate
        total_speech = sum(s.duration for s in segments)
        speech_ratio = total_speech / total_duration if total_duration > 0 else 0.0

        result = VADResult(
            segments=segments,
            total_speech_duration=round(total_speech, 3),
            speech_ratio=round(speech_ratio, 4),
        )

        logger.info(
            "VAD complete — %d segments | %.1f s speech | %.1f%% ratio",
            len(segments),
            total_speech,
            speech_ratio * 100,
        )
        return result

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _ensure_model_loaded(self) -> None:
        """
        Lazily load the Silero VAD model from torch.hub.

        Uses ``trust_repo=True`` to suppress the interactive prompt added
        in PyTorch ≥ 1.12.

        Raises:
            VADError: If the model cannot be loaded.
        """
        if self._model is not None:
            return  # already loaded

        logger.info("Loading Silero VAD model from torch.hub…")
        try:
            model, utils = torch.hub.load(
                repo_or_dir=_SILERO_REPO,
                model=_SILERO_MODEL,
                force_reload=False,
                trust_repo=True,
            )
            self._model = model
            # ``utils`` is a named-tuple; we only need get_speech_timestamps.
            (
                self._get_speech_timestamps,
                _,  # save_audio
                _,  # read_audio
                _,  # VADIterator
                _,  # collect_chunks
            ) = utils
            logger.info("Silero VAD model loaded.")
        except Exception as exc:
            raise VADError(f"Failed to load Silero VAD model: {exc}") from exc

    @staticmethod
    def _load_audio(wav_path: str) -> tuple[torch.Tensor, int]:
        """
        Load a WAV file as a 1-D float32 PyTorch tensor.

        Multi-channel files are mixed down to mono before returning.

        Returns:
            (tensor, sample_rate) — a 1-D CPU float32 tensor.

        Raises:
            AudioLoadError: On any read failure.
        """
        try:
            data, sample_rate = sf.read(wav_path, dtype="float32", always_2d=True)
        except Exception as exc:
            raise AudioLoadError(f"Cannot read '{wav_path}' for VAD: {exc}") from exc

        # Mix-down to mono if needed.
        mono: np.ndarray = data.mean(axis=1)
        tensor = torch.from_numpy(mono)
        return tensor, int(sample_rate)

    @staticmethod
    def _convert_segments(
        raw: list[dict[str, int]], sample_rate: int
    ) -> list[VADSegment]:
        """
        Convert Silero's sample-indexed segments to second-indexed VADSegments.

        Parameters:
            raw: List of ``{"start": int, "end": int}`` dicts (sample indices).
            sample_rate: Sample rate of the audio used for inference.

        Returns:
            Sorted list of :class:`~audio_preprocessor.models.VADSegment`.
        """
        segments = [
            VADSegment(
                start=round(seg["start"] / sample_rate, 3),
                end=round(seg["end"] / sample_rate, 3),
            )
            for seg in raw
        ]
        segments.sort(key=lambda s: s.start)
        return segments
