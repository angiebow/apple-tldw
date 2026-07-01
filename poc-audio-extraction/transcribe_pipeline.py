"""
transcribe_pipeline.py
======================
VAD-guided Whisper transcription, built on top of the audio_preprocessor pipeline.

    video / audio file
        → AudioPreprocessor (FFmpeg extract → denoise → AGC → loudness norm → VAD)
        → OpenAI Whisper STT over each detected speech segment
        → { text, segments, … }

This is the library entry point used by the tldw backend's ``POST /transcribe``
endpoint so the macOS app can turn a raw recording straight into a transcript
that feeds the highlighter. It is a function-level distillation of the standalone
``run_whisper.py`` CLI: same VAD-guided slicing (which speeds inference and avoids
Whisper hallucinating over silence), but returning text in-memory instead of
writing JSON sidecars.

Whisper (and torchaudio) are imported lazily so that importing this module — and
the rest of the backend — stays cheap; the heavy weights download only on the
first transcription request.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

# Make the sibling ``audio_preprocessor`` package importable when this module is
# loaded by path (the backend inserts this folder onto sys.path, mirroring the
# blooper detector's integration).
ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audio_preprocessor import AudioPreprocessor, PreprocessorConfig  # noqa: E402
from audio_preprocessor.config import FFmpegConfig, VADConfig  # noqa: E402

# Minimum samples Whisper needs before a slice is worth transcribing (10 ms @ 16 kHz).
_MIN_SLICE_SAMPLES = 160

# Cache loaded Whisper models by (name, device) so repeat requests don't reload.
_whisper_models: Dict[tuple, Any] = {}


def _openai_device() -> str:
    """Pick the best available device for the PyTorch Whisper backend."""
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _load_whisper(model_name: str, device: str):
    """Load (and cache) an OpenAI Whisper model, falling back to CPU if the
    accelerator can't host it (MPS lacks some sparse ops on older wheels)."""
    import whisper

    key = (model_name, device)
    if key in _whisper_models:
        return _whisper_models[key], device
    try:
        model = whisper.load_model(model_name, device=device)
    except (NotImplementedError, RuntimeError):
        device = "cpu"
        key = (model_name, device)
        if key in _whisper_models:
            return _whisper_models[key], device
        model = whisper.load_model(model_name, device="cpu")
    _whisper_models[key] = model
    return model, device


def _build_config(output_dir: str) -> PreprocessorConfig:
    """Preprocessor config tuned for STT: canonical 16 kHz mono WAV + VAD."""
    return PreprocessorConfig(
        output_dir=output_dir,
        log_level="WARNING",  # keep the backend log quiet; the app shows its own status
        ffmpeg=FFmpegConfig(sample_rate=16_000, channels=1, codec="pcm_s16le"),
        vad=VADConfig(
            threshold=0.5,
            min_speech_duration_ms=250,
            min_silence_duration_ms=500,
        ),
    )


def transcribe(input_path: str, model_name: str = "base") -> Dict[str, Any]:
    """Preprocess *input_path* and transcribe it with VAD-guided Whisper.

    Parameters:
        input_path: Absolute/relative path to a local video or audio file.
        model_name: Whisper model size ("tiny", "base", "small", "medium", "large").

    Returns a JSON-serialisable dict:
        {
            "text":        full transcript (segments joined),
            "segments":    [{ "id", "start", "end", "text" }, …]  (source timeline),
            "duration_s":  source duration in seconds,
            "speech_ratio": fraction of audio that was speech,
            "model":       the Whisper model used,
        }

    Raises:
        FileNotFoundError:       if *input_path* does not exist.
        UnsupportedFormatError:  if the extension isn't a supported A/V container.
        AudioPreprocessorError:  on any preprocessing-stage failure.
    """
    input_path = os.path.abspath(os.path.expanduser(input_path))
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: '{input_path}'")

    # Preprocess into a scratch dir; we only need the WAV + VAD in memory.
    with tempfile.TemporaryDirectory(prefix="tldw_transcribe_") as tmp_dir:
        result = AudioPreprocessor(_build_config(tmp_dir)).process(input_path)

        import torchaudio

        waveform, sample_rate = torchaudio.load(result.preprocessed_audio_path)
        channel = waveform[0]  # preprocessed audio is mono

        device = _openai_device()
        model, device = _load_whisper(model_name, device)

        out_segments: List[Dict[str, Any]] = []
        texts: List[str] = []
        seg_id = 0

        for seg in result.vad_result.segments:
            start_sample = int(seg.start * sample_rate)
            end_sample = int(seg.end * sample_rate)
            chunk = channel[start_sample:end_sample].numpy().astype("float32")
            if len(chunk) < _MIN_SLICE_SAMPLES:
                continue

            try:
                res = model.transcribe(chunk, fp16=False)
            except (NotImplementedError, RuntimeError):
                # Accelerator choked mid-run — drop to CPU and retry this slice.
                if device != "cpu":
                    model, device = _load_whisper(model_name, "cpu")
                    res = model.transcribe(chunk, fp16=False)
                else:
                    raise

            # Whisper timestamps are relative to the slice; offset back to the
            # original timeline using the VAD segment's start.
            for s in res.get("segments", []):
                text = s["text"].strip()
                if not text:
                    continue
                out_segments.append({
                    "id": seg_id,
                    "start": round(seg.start + s["start"], 3),
                    "end": round(seg.start + s["end"], 3),
                    "text": text,
                })
                seg_id += 1
                texts.append(text)

    return {
        "text": " ".join(texts).strip(),
        "segments": out_segments,
        "duration_s": round(result.audio_metrics.duration_seconds, 3),
        "speech_ratio": round(result.vad_result.speech_ratio, 4),
        "model": model_name,
    }
