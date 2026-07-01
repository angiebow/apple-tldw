"""
Automatic Gain Control (AGC) / Dynamic Range Compression (DRC)
==============================================================
Applies dynamic volume levelling to the audio signal using FFmpeg's
`dynaudnorm` (Dynamic Audio Normalizer) filter.

This stage is positioned after denoising and before integrated loudness normalisation:
    1. Denoise (deepfilterlib)  -> Remove background noise so AGC doesn't boost it.
    2. AGC (dynaudnorm)         -> Level internal volume dynamics (quiet parts boosted, loud controlled).
    3. Loudness Norm (pyln)     -> Conform the leveled track to exact integrated -23 LUFS.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

from .config import AGCConfig
from .exceptions import AGCError

logger = logging.getLogger(__name__)


class AudioAGC:
    """
    Applies Dynamic Audio Normalisation to a WAV file using FFmpeg.

    Parameters:
        config: :class:`~audio_preprocessor.config.AGCConfig` controlling
                filter settings.
        ffmpeg_path: Path to the FFmpeg binary (usually inherited from
                     FFmpegExtractor/FFmpegConfig).
    """

    def __init__(self, config: AGCConfig, ffmpeg_path: str = "ffmpeg") -> None:
        self._cfg = config
        self._ffmpeg_bin = self._resolve_ffmpeg(ffmpeg_path)

    def process(self, input_path: str, output_path: str) -> str:
        """
        Apply dynaudnorm dynamic range compression to *input_path*
        and save the result to *output_path*.

        Parameters:
            input_path: Path to the source WAV file.
            output_path: Destination path for the processed WAV file.

        Returns:
            Absolute path to the processed WAV file.

        Raises:
            AGCError: If dynamic normalisation fails or FFmpeg cannot run.
        """
        if not self._cfg.enabled:
            logger.info("AGC disabled — copying input to output.")
            shutil.copy2(input_path, output_path)
            return output_path

        input_path = os.path.abspath(input_path)
        output_path = os.path.abspath(output_path)

        filter_str = (
            f"dynaudnorm=f={self._cfg.frame_len_ms}"
            f":g={self._cfg.filter_size}"
            f":m={self._cfg.max_gain:.1f}"
            f":p={self._cfg.target_peak:.2f}"
        )

        cmd = [
            self._ffmpeg_bin,
            "-y",
            "-i", input_path,
            "-af", filter_str,
            output_path
        ]

        logger.info(
            "Applying AGC: '%s' → '%s' with filter '%s'",
            input_path,
            output_path,
            filter_str,
        )
        logger.debug("FFmpeg AGC command: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise AGCError(f"FFmpeg binary not found at '{self._ffmpeg_bin}': {exc}") from exc

        if result.returncode != 0:
            raise AGCError(
                f"FFmpeg AGC failed (exit {result.returncode}).\n"
                f"Command: {' '.join(cmd)}\n"
                f"Stderr:\n{result.stderr}"
            )

        logger.info("AGC process complete.")
        return output_path

    @staticmethod
    def _resolve_ffmpeg(ffmpeg_path: str) -> str:
        """Resolve absolute path of the FFmpeg binary."""
        if os.path.isabs(ffmpeg_path):
            if os.path.isfile(ffmpeg_path) and os.access(ffmpeg_path, os.X_OK):
                return ffmpeg_path
            raise AGCError(f"FFmpeg binary not found at absolute path: '{ffmpeg_path}'")

        resolved = shutil.which(ffmpeg_path)
        if resolved is None:
            raise AGCError(f"FFmpeg binary '{ffmpeg_path}' not found in PATH.")
        return resolved
