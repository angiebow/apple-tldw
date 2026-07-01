"""
Exceptions Module
=================
Custom exception hierarchy for the audio preprocessing pipeline.
Using a hierarchy (rather than bare ``RuntimeError``) lets callers catch
specific failure modes without importing third-party library exceptions.
"""

from __future__ import annotations


class AudioPreprocessorError(Exception):
    """Base class for all audio-preprocessor exceptions."""


class UnsupportedFormatError(AudioPreprocessorError):
    """Raised when an input file has an unsupported extension or MIME type."""

    def __init__(self, path: str, supported: tuple[str, ...]) -> None:
        super().__init__(
            f"Unsupported file format: '{path}'. "
            f"Supported formats: {', '.join(supported)}"
        )
        self.path = path
        self.supported = supported


class FFmpegError(AudioPreprocessorError):
    """Raised when FFmpeg exits with a non-zero return code."""

    def __init__(self, cmd: str, returncode: int, stderr: str) -> None:
        super().__init__(
            f"FFmpeg failed (exit {returncode}).\n"
            f"Command: {cmd}\n"
            f"Stderr:\n{stderr}"
        )
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr


class FFmpegNotFoundError(AudioPreprocessorError):
    """Raised when the ffmpeg binary cannot be located."""

    def __init__(self, ffmpeg_path: str) -> None:
        super().__init__(
            f"FFmpeg binary not found at '{ffmpeg_path}'. "
            "Install FFmpeg and ensure it is on $PATH, or set "
            "PreprocessorConfig.ffmpeg.ffmpeg_path to the correct location."
        )
        self.ffmpeg_path = ffmpeg_path


class AudioLoadError(AudioPreprocessorError):
    """Raised when soundfile / scipy cannot load the extracted WAV."""


class QualityAssessmentError(AudioPreprocessorError):
    """Raised when audio quality metrics cannot be computed."""


class DenoiserError(AudioPreprocessorError):
    """Raised when the denoiser backend encounters an unrecoverable error."""


class VADError(AudioPreprocessorError):
    """Raised when the Silero VAD model fails to load or run inference."""


class OutputError(AudioPreprocessorError):
    """Raised when output files cannot be written to the target directory."""


class AGCError(AudioPreprocessorError):
    """Raised when Automatic Gain Control / dynamic range compression fails."""
