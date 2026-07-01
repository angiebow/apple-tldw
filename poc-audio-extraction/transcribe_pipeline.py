"""
transcribe_pipeline.py
======================
Preprocess a recording, then transcribe it with **single-pass MLX Whisper**.

    video / audio file
        → AudioPreprocessor (FFmpeg extract → denoise → AGC → loudness norm → VAD)
        → hans-development's WhisperTranscriber (single-pass full-file MLX Whisper
          + hallucination sanitizer)
        → { text, segments (with per-word times), … }

This is the library entry point used by the tldw backend's ``POST /transcribe``
endpoint. It replaces the earlier VAD-sliced OpenAI-Whisper approach with the
``poc-transcription`` module from the hans-development branch: MLX Whisper slides
its own 30 s window across the whole waveform (better long-form segment
timestamps than manual pre-slicing) and a sanitizer strips decoder-hallucination
artifacts before anything is returned.

MLX and the transcription package are imported lazily so importing this module —
and the rest of the backend — stays cheap; the model downloads on the first call.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# The audio_preprocessor package lives beside this file; hans's transcription
# package (audio_stt) lives in the sibling poc-transcription/ folder. Put both on
# sys.path so path-based loading by the backend resolves their imports.
ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TRANSCRIPTION_DIR = (ROOT.parent / "poc-transcription").resolve()
if str(TRANSCRIPTION_DIR) not in sys.path:
    sys.path.insert(0, str(TRANSCRIPTION_DIR))

from audio_preprocessor import AudioPreprocessor, PreprocessorConfig  # noqa: E402
from audio_preprocessor.config import FFmpegConfig, VADConfig  # noqa: E402

_DEFAULT_MLX_MODEL = "mlx-community/whisper-large-v3-turbo"

# Friendly size names → MLX-community Whisper repos (full repo ids pass through).
_MLX_MODELS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large": "mlx-community/whisper-large-v3-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}


def _resolve_mlx_model(name: Optional[str]) -> str:
    """Map a friendly size ("turbo", "base") to an MLX repo; pass full ids through."""
    if not name:
        return _DEFAULT_MLX_MODEL
    name = name.strip()
    if "/" in name:
        return name
    return _MLX_MODELS.get(name.lower(), f"mlx-community/whisper-{name}-mlx")


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


def transcribe(input_path: str, model_name: Optional[str] = None) -> Dict[str, Any]:
    """Preprocess *input_path* and transcribe it with single-pass MLX Whisper.

    Parameters:
        input_path: Absolute/relative path to a local video or audio file.
        model_name: Whisper size ("turbo", "base", …) or a full MLX repo id;
                    defaults to whisper-large-v3-turbo.

    Returns a JSON-serialisable dict:
        {
            "text":        full transcript (segments joined),
            "segments":    [{ "id", "start", "end", "text", "words":[…] }, …],
            "duration_s":  source duration in seconds,
            "speech_ratio": fraction of audio that was speech,
            "model":       the MLX Whisper repo used,
        }

    Raises:
        FileNotFoundError:       if *input_path* does not exist.
        UnsupportedFormatError:  if the extension isn't a supported A/V container.
        AudioPreprocessorError:  on any preprocessing-stage failure.
    """
    input_path = os.path.abspath(os.path.expanduser(input_path))
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: '{input_path}'")

    model_repo = _resolve_mlx_model(model_name)

    # Preprocess into a scratch dir; MLX Whisper runs on the preprocessed WAV.
    with tempfile.TemporaryDirectory(prefix="tldw_transcribe_") as tmp_dir:
        result = AudioPreprocessor(_build_config(tmp_dir)).process(input_path)

        from audio_stt.config import WhisperConfig
        from audio_stt.transcriber import WhisperTranscriber

        transcriber = WhisperTranscriber(
            audio_path=result.preprocessed_audio_path,
            config=WhisperConfig(model=model_repo),
        )
        segments, words = transcriber.run()   # single pass + hallucination sanitizer

    # Nest each segment's words back under it (sanitizer already dropped the words
    # of any segment it removed, so segment_ids line up).
    words_by_seg: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for w in words:
        words_by_seg[w.segment_id].append(
            {"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)})

    out_segments: List[Dict[str, Any]] = []
    texts: List[str] = []
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        out_segments.append({
            "id": s.id,
            "start": round(s.start, 3),
            "end": round(s.end, 3),
            "text": text,
            "words": words_by_seg.get(s.id, []),
        })
        texts.append(text)

    return {
        "text": " ".join(texts).strip(),
        "segments": out_segments,
        "duration_s": round(result.audio_metrics.duration_seconds, 3),
        "speech_ratio": round(result.vad_result.speech_ratio, 4),
        "model": model_repo,
    }
