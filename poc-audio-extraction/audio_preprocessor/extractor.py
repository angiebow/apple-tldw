"""
FFmpeg Extractor
================
Responsible for converting any supported video/audio input into a
canonical 16 kHz, mono, PCM-S16LE WAV file using FFmpeg as the backend.

Design notes:
    - Runs FFmpeg as a subprocess so there is no FFmpeg Python-binding
      dependency.  This keeps the dependency tree thin and guarantees
      compatibility with FFmpeg builds distributed via system package
      managers, conda, or Homebrew.
    - Input format detection is based solely on file extension so that
      no external ``python-magic`` dependency is required.  Extending
      to MIME-based detection is straightforward if needed.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .config import FFmpegConfig
from .exceptions import FFmpegError, FFmpegNotFoundError, UnsupportedFormatError

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────── #
# Supported formats                                                             #
# ──────────────────────────────────────────────────────────────────────────── #

#: Extensions treated as video containers (audio stream extracted by FFmpeg).
VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mov", ".mkv", ".avi", ".webm"})

#: Extensions treated as raw audio files (resampled/re-encoded if needed).
AUDIO_EXTENSIONS: frozenset[str] = frozenset({".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac"})

#: Union of all accepted extensions.
SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(VIDEO_EXTENSIONS | AUDIO_EXTENSIONS)


class FFmpegExtractor:
    """
    Wraps FFmpeg to extract and normalise audio from video or audio inputs.

    Parameters:
        config: :class:`~audio_preprocessor.config.FFmpegConfig` instance
                controlling sample rate, codec, channel count, etc.
    """

    def __init__(self, config: FFmpegConfig) -> None:
        self._cfg = config
        self._ffmpeg_bin = self._resolve_ffmpeg(config.ffmpeg_path)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def extract(self, input_path: str, output_path: str) -> str:
        """
        Convert *input_path* to a canonical WAV file at *output_path*.

        Handles both video containers (strips video stream) and audio files
        (resamples to target format).  The output is always:
            • PCM signed 16-bit little-endian
            • 16 000 Hz sample rate
            • Mono (1 channel)

        Parameters:
            input_path: Absolute or relative path to the source file.
            output_path: Destination WAV path (parent directory must exist).

        Returns:
            Absolute path to the written WAV file.

        Raises:
            UnsupportedFormatError: If *input_path* has an unsupported extension.
            FFmpegNotFoundError: If the FFmpeg binary cannot be found.
            FFmpegError: If FFmpeg exits with a non-zero return code.
        """
        input_path = os.path.abspath(input_path)
        output_path = os.path.abspath(output_path)

        self._validate_extension(input_path)
        self._ensure_parent_dir(output_path)

        logger.info("Extracting audio: '%s' → '%s'", input_path, output_path)

        cmd = self._build_command(input_path, output_path)
        logger.debug("FFmpeg command: %s", " ".join(cmd))

        self._run(cmd)

        logger.info(
            "Extraction complete. Output: '%s' (%.1f KB)",
            output_path,
            os.path.getsize(output_path) / 1024,
        )
        return output_path

    @property
    def is_available(self) -> bool:
        """Return ``True`` if the FFmpeg binary is reachable."""
        return self._ffmpeg_bin is not None

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_ffmpeg(ffmpeg_path: str) -> str:
        """
        Resolve the FFmpeg binary path.

        Raises:
            FFmpegNotFoundError: If the binary cannot be located.
        """
        # If an explicit path was given, check it directly.
        if os.path.isabs(ffmpeg_path):
            if os.path.isfile(ffmpeg_path) and os.access(ffmpeg_path, os.X_OK):
                return ffmpeg_path
            raise FFmpegNotFoundError(ffmpeg_path)

        # Otherwise probe $PATH.
        resolved = shutil.which(ffmpeg_path)
        if resolved is None:
            raise FFmpegNotFoundError(ffmpeg_path)
        return resolved

    def _validate_extension(self, path: str) -> None:
        """
        Ensure the file extension is in the supported set.

        Raises:
            UnsupportedFormatError: On unsupported extension.
        """
        ext = Path(path).suffix.lower()
        if ext not in VIDEO_EXTENSIONS and ext not in AUDIO_EXTENSIONS:
            raise UnsupportedFormatError(path, SUPPORTED_EXTENSIONS)

    @staticmethod
    def _ensure_parent_dir(path: str) -> None:
        """Create parent directories for *path* if they do not exist."""
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _build_command(self, input_path: str, output_path: str) -> list[str]:
        """
        Construct the FFmpeg command list.

        The ``-vn`` flag strips any video stream.  This is harmless for
        audio-only inputs (FFmpeg simply ignores it).
        """
        cmd: list[str] = [self._ffmpeg_bin]
        if self._cfg.overwrite:
            cmd.append("-y")
        cmd.extend([
            "-i", input_path,
            "-vn",                          # discard video stream
            "-acodec", self._cfg.codec,
            "-ar", str(self._cfg.sample_rate),
            "-ac", str(self._cfg.channels),
            output_path,
        ])
        return cmd

    @staticmethod
    def _run(cmd: list[str]) -> None:
        """
        Execute *cmd* and raise :class:`FFmpegError` on failure.

        Stderr is captured and included in the exception message to aid
        debugging without polluting the caller's stdout.
        """
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise FFmpegNotFoundError(cmd[0]) from exc

        if result.returncode != 0:
            raise FFmpegError(
                cmd=" ".join(cmd),
                returncode=result.returncode,
                stderr=result.stderr,
            )
