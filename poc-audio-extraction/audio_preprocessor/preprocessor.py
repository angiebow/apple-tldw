"""
Audio Preprocessor – Pipeline Orchestrator
==========================================
The :class:`AudioPreprocessor` class is the single public entry point for
the audio preprocessing module.  It wires together all pipeline stages and
exposes a clean, single-method API:

    preprocessor = AudioPreprocessor(config)
    result = preprocessor.process("podcast.mp4")

Pipeline stages executed in order:
    1. Validate input (extension, file existence)
    2. Extract audio via FFmpeg → canonical WAV
    3. Assess audio quality (SNR, RMS, flatness, clipping)
    4. Decide whether and how to denoise (adaptive SNR policy)
    5. Optionally apply DeepFilterNet denoising
    6. Loudness normalisation (pyloudnorm, EBU R128)   ← NEW
    7. Run Silero VAD → speech-segment timestamps
    8. Write outputs (WAV + JSON sidecars)
    9. Return :class:`~audio_preprocessor.models.PreprocessingResult`

Stage order rationale:
    Denoise → Normalize → VAD
    • Denoising first removes noise that would inflate LUFS readings.
    • Normalisation gives Silero VAD a consistent loudness level, improving
      detection accuracy on quiet or loud recordings.
    • The output audio passed to Whisper is clean AND at broadcast loudness.

Thread safety:
    Each :class:`AudioPreprocessor` instance maintains its own pipeline
    components.  Creating one instance per worker is recommended for
    parallel workloads; sharing an instance across threads is NOT safe
    due to VAD model state.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from .agc import AudioAGC
from .config import DenoiseStrength, PreprocessorConfig
from .denoiser import DenoiserFactory, decide_denoising
from .exceptions import AudioPreprocessorError, UnsupportedFormatError
from .extractor import SUPPORTED_EXTENSIONS, FFmpegExtractor
from .models import NormalizationResult, PreprocessingResult
from .normalizer import LoudnessNormalizer
from .quality import AudioQualityAssessor
from .vad import SileroVAD
from .writer import OutputWriter

logger = logging.getLogger(__name__)


class AudioPreprocessor:
    """
    High-level orchestrator for the audio preprocessing pipeline.

    Parameters:
        config: Optional :class:`~audio_preprocessor.config.PreprocessorConfig`.
                If omitted, all defaults are used.

    Example::

        from audio_preprocessor import AudioPreprocessor, PreprocessorConfig

        config = PreprocessorConfig(output_dir="output/")
        preprocessor = AudioPreprocessor(config)
        result = preprocessor.process("podcast.mp4")
        print(result)
    """

    def __init__(self, config: Optional[PreprocessorConfig] = None) -> None:
        self._cfg = config or PreprocessorConfig()
        self._configure_logging()

        # Instantiate pipeline components.
        self._extractor = FFmpegExtractor(self._cfg.ffmpeg)
        self._assessor = AudioQualityAssessor(self._cfg.quality)
        self._denoiser = DenoiserFactory.create(self._cfg.denoise)
        self._agc = AudioAGC(self._cfg.agc, self._cfg.ffmpeg.ffmpeg_path)
        self._normalizer = LoudnessNormalizer(self._cfg.normalize)
        self._vad = SileroVAD(self._cfg.vad)
        self._writer = OutputWriter(self._cfg)

        logger.info(
            "AudioPreprocessor initialised | denoiser=%s | agc=%s | normalise=%s (%.1f LUFS) | output_dir=%s",
            self._denoiser.name,
            self._cfg.agc.enabled,
            self._cfg.normalize.enabled,
            self._cfg.normalize.target_lufs,
            self._cfg.output_path,
        )

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def process(self, input_path: str) -> PreprocessingResult:
        """
        Run the full preprocessing pipeline on *input_path*.

        Accepts MP4, MOV, WAV, MP3, and several other common formats
        (see :data:`~audio_preprocessor.extractor.SUPPORTED_EXTENSIONS`
        for the full list).

        Parameters:
            input_path: Path to the source video or audio file.

        Returns:
            :class:`~audio_preprocessor.models.PreprocessingResult` containing
            paths to all output artefacts and all intermediate metrics.

        Raises:
            FileNotFoundError: If *input_path* does not exist.
            UnsupportedFormatError: If the file extension is not supported.
            AudioPreprocessorError: On any pipeline stage failure.
        """
        input_path = os.path.abspath(input_path)
        self._validate_input(input_path)

        wall_start = time.perf_counter()
        logger.info("━" * 60)
        logger.info("Processing: '%s'", input_path)

        with tempfile.TemporaryDirectory(prefix="audio_preproc_") as tmp_dir:
            result = self._run_pipeline(input_path, tmp_dir)

        elapsed = time.perf_counter() - wall_start
        logger.info("Pipeline complete in %.2f s", elapsed)
        logger.info("━" * 60)

        elapsed_rounded = round(elapsed, 3)
        # Patch the final timing into the result object.
        object.__setattr__(result, "processing_time_seconds", elapsed_rounded)  # type: ignore[arg-type]

        # Update the report JSON on disk with the final timing.
        if result.report_path and os.path.exists(result.report_path):
            import json
            try:
                with open(result.report_path, "r", encoding="utf-8") as f:
                    report_data = json.load(f)
                report_data["processing_time_seconds"] = elapsed_rounded
                with open(result.report_path, "w", encoding="utf-8") as f:
                    json.dump(report_data, f, indent=2)
            except Exception as e:
                logger.warning("Failed to update processing time in report file: %s", e)

        return result

    # ------------------------------------------------------------------ #
    # Pipeline internals                                                   #
    # ------------------------------------------------------------------ #

    def _run_pipeline(self, input_path: str, tmp_dir: str) -> PreprocessingResult:
        """
        Execute all pipeline stages within a temporary scratch directory.

        Using a temp dir means all intermediate files are automatically
        cleaned up when the context manager exits, regardless of success
        or failure.
        """
        # ── Stage 1: Extract audio ─────────────────────────────────────
        logger.info("[1/8] Extracting audio…")
        extracted_wav = os.path.join(tmp_dir, "extracted.wav")
        self._extractor.extract(input_path, extracted_wav)

        # ── Stage 2: Quality assessment ────────────────────────────────
        logger.info("[2/8] Assessing audio quality…")
        metrics = self._assessor.assess(extracted_wav)

        # ── Stage 3: Adaptive denoising decision ───────────────────────
        logger.info("[3/8] Making denoising decision…")
        decision = decide_denoising(metrics, self._cfg.denoise)
        logger.info(
            "Decision: denoise=%s | strength=%s | %s",
            decision.should_denoise,
            decision.strength,
            decision.reason,
        )

        # ── Stage 4: Denoise (conditional) ────────────────────────────
        logger.info("[4/8] Denoising…")
        denoised_wav = os.path.join(tmp_dir, "denoised.wav")
        denoise_executed = False
        if decision.should_denoise:
            strength = DenoiseStrength(decision.strength)
            self._denoiser.denoise(extracted_wav, denoised_wav, strength)
            # NullDenoiser is a no-op pass-through, not real denoising.
            denoise_executed = self._denoiser.name != "NullDenoiser"
            if denoise_executed:
                # DeepFilterNet always outputs at its model sample rate (48 kHz).
                # Resample the denoised file back to the canonical target sample rate.
                logger.info(
                    "Resampling denoised audio from 48 kHz back to %d Hz…",
                    self._cfg.ffmpeg.sample_rate,
                )
                resampled_tmp = os.path.join(tmp_dir, "denoised_resampled.wav")
                self._extractor.extract(denoised_wav, resampled_tmp)
                shutil.move(resampled_tmp, denoised_wav)
        else:
            logger.info("Denoising skipped (audio is clean).")
            shutil.copy2(extracted_wav, denoised_wav)

        # ── Stage 5: AGC / Dynamic Range Compression ──────────────────
        logger.info("[5/8] Applying AGC (volume levelling)…")
        agc_wav = os.path.join(tmp_dir, "agc.wav")
        self._agc.process(denoised_wav, agc_wav)

        # ── Stage 6: Loudness normalisation ───────────────────────────
        logger.info("[6/8] Normalising loudness…")
        normalised_wav = os.path.join(tmp_dir, "normalised.wav")
        if self._cfg.normalize.enabled:
            norm_result = self._normalizer.normalize(agc_wav, normalised_wav)
        else:
            logger.info("Normalisation disabled — skipping.")
            shutil.copy2(agc_wav, normalised_wav)
            norm_result = NormalizationResult(
                input_lufs=float("nan"),
                output_lufs=float("nan"),
                target_lufs=self._cfg.normalize.target_lufs,
                gain_db=0.0,
                clipping_prevented=False,
                normalization_applied=False,
            )

        # ── Stage 7: VAD ───────────────────────────────────────────────
        logger.info("[7/8] Running voice activity detection…")
        vad_result = self._vad.detect(normalised_wav)

        # ── Stage 8: Write outputs ─────────────────────────────────────
        logger.info("[8/8] Writing output artefacts…")
        partial_result = PreprocessingResult(
            preprocessed_audio_path="",  # filled after write
            vad_metadata_path="",
            report_path="",
            audio_metrics=metrics,
            denoise_decision=decision,
            normalization_result=norm_result,
            vad_result=vad_result,
            processing_time_seconds=0.0,  # filled after wall-clock measurement
            input_path=input_path,
            denoise_executed=denoise_executed,
            denoiser_backend=self._denoiser.name,
            agc_applied=self._cfg.agc.enabled,
        )
        audio_dst, vad_dst, report_dst = self._writer.write(normalised_wav, partial_result)

        return PreprocessingResult(
            preprocessed_audio_path=audio_dst,
            vad_metadata_path=vad_dst,
            report_path=report_dst,
            audio_metrics=metrics,
            denoise_decision=decision,
            normalization_result=norm_result,
            vad_result=vad_result,
            processing_time_seconds=0.0,  # overwritten by caller
            input_path=input_path,
            denoise_executed=denoise_executed,
            denoiser_backend=self._denoiser.name,
            agc_applied=self._cfg.agc.enabled,
        )

    # ------------------------------------------------------------------ #
    # Validation & setup                                                   #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_input(path: str) -> None:
        """
        Ensure the input file exists and has a supported extension.

        Raises:
            FileNotFoundError: If the file does not exist.
            UnsupportedFormatError: If the extension is not supported.
        """
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Input file not found: '{path}'")
        ext = Path(path).suffix.lower()
        if ext not in {e for e in SUPPORTED_EXTENSIONS}:
            raise UnsupportedFormatError(path, SUPPORTED_EXTENSIONS)

    def _configure_logging(self) -> None:
        """Set up structured logging at the configured level."""
        numeric_level = getattr(logging, self._cfg.log_level.upper(), logging.INFO)
        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        # Suppress noisy third-party loggers.
        logging.getLogger("torch").setLevel(logging.WARNING)
        logging.getLogger("torchaudio").setLevel(logging.WARNING)
        logging.getLogger("df").setLevel(logging.WARNING)
