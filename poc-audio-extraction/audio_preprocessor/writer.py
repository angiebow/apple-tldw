"""
Output Writer
=============
Handles all file I/O for the pipeline's output artefacts:
    • preprocessed.wav      – the final processed audio
    • vad_metadata.json     – speech segment timestamps
    • processing_report.json – full processing report

Keeping I/O in a dedicated class allows the rest of the pipeline to remain
stateless and makes the write step easily mockable in unit tests.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict

from .config import PreprocessorConfig
from .exceptions import OutputError
from .models import PreprocessingResult

logger = logging.getLogger(__name__)


class OutputWriter:
    """
    Writes the three output artefacts produced by the preprocessing pipeline.

    Parameters:
        config: Top-level pipeline configuration (output paths are read from here).
    """

    def __init__(self, config: PreprocessorConfig) -> None:
        self._cfg = config

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def write(
        self,
        processed_wav_path: str,
        result: PreprocessingResult,
    ) -> tuple[str, str, str]:
        """
        Copy *processed_wav_path* to the configured output directory and
        write the JSON sidecar files.

        Parameters:
            processed_wav_path: Path to the denoised (or pass-through) WAV.
            result: Partially-populated :class:`~audio_preprocessor.models.PreprocessingResult`
                    whose ``audio_metrics``, ``denoise_decision``, and
                    ``vad_result`` are already set.

        Returns:
            3-tuple of absolute paths:
                (preprocessed_wav, vad_metadata_json, processing_report_json)

        Raises:
            OutputError: If any write operation fails.
        """
        self._ensure_output_dir()

        audio_dst = self._write_audio(processed_wav_path)
        vad_dst = self._write_json(
            filename=self._cfg.vad_metadata_filename,
            data=result.to_vad_dict(),
        )
        report_dst = self._write_json(
            filename=self._cfg.report_filename,
            data=result.to_report_dict(),
        )

        logger.info(
            "Outputs written:\n  audio   → %s\n  vad     → %s\n  report  → %s",
            audio_dst,
            vad_dst,
            report_dst,
        )
        return audio_dst, vad_dst, report_dst

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _ensure_output_dir(self) -> None:
        """Create the output directory tree if it does not exist."""
        try:
            os.makedirs(self._cfg.output_path, exist_ok=True)
        except OSError as exc:
            raise OutputError(
                f"Cannot create output directory '{self._cfg.output_path}': {exc}"
            ) from exc

    def _write_audio(self, src: str) -> str:
        """
        Copy *src* WAV to the configured output filename.

        Returns:
            Absolute path to the destination file.
        """
        dst = self._cfg.resolve_output(self._cfg.preprocessed_filename)
        try:
            shutil.copy2(src, dst)
        except OSError as exc:
            raise OutputError(
                f"Failed to copy audio '{src}' → '{dst}': {exc}"
            ) from exc
        logger.debug("Audio written: %s (%.1f KB)", dst, os.path.getsize(dst) / 1024)
        return dst

    def _write_json(self, filename: str, data: Dict[str, Any]) -> str:
        """
        Serialise *data* to a JSON file in the output directory.

        Parameters:
            filename: Basename of the output file (e.g. ``"vad_metadata.json"``).
            data: JSON-serialisable dictionary.

        Returns:
            Absolute path to the written file.
        """
        dst = self._cfg.resolve_output(filename)
        try:
            with open(dst, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.write("\n")  # POSIX newline at EOF
        except OSError as exc:
            raise OutputError(f"Failed to write '{dst}': {exc}") from exc
        logger.debug("JSON written: %s", dst)
        return dst
