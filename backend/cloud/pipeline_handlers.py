"""Real ML job handlers — the async, storage-backed ports of server.py's
/transcribe, /bloopers, /clip and /merge endpoints.

Design rules that keep the scaffold bootable everywhere:
  * No heavy imports at module top. torch / whisper / the PoC modules are
    imported lazily inside each handler (same trick server.py uses), so merely
    importing this module — and registering the handlers — needs only stdlib.
  * Each handler pulls its input from storage to a scratch path, runs the exact
    same PoC pipeline call the local endpoint used, then uploads any produced
    media back to storage. Inline JSON (transcript, spans) rides in Result.data.

If the ML deps are missing at run time the job fails with a clear error; the API
and other jobs stay up.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

from .worker import JobContext, Result, register

# PoC module locations (mirrors server.py). backend/ is the parent of cloud/.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POC_AUDIO_DIR = os.path.join(_BACKEND_DIR, "..", "poc-audio-extraction")
POC_BLOOPER_DIR = os.path.join(_BACKEND_DIR, "..", "poc-blooper-detector")
POC_CLIPPER_DIR = os.path.join(_BACKEND_DIR, "..", "poc-video-clipper")
POC_SUBTITLES_DIR = os.path.join(_BACKEND_DIR, "..", "poc-subtitles")

WHISPER_MODEL = os.environ.get("TLDW_WHISPER_MODEL", "turbo")

# Cache the deferred PoC modules across jobs (loaded once per worker process).
_mods: Dict[str, Any] = {}


def _load(name: str, poc_dir: str):
    if name not in _mods:
        if poc_dir not in sys.path:
            sys.path.insert(0, poc_dir)
        _mods[name] = __import__(name)
    return _mods[name]


def _flatten_words(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Same flattening server.py does: gather every word from every segment."""
    return [{"word": w["word"], "start": w["start"], "end": w["end"]}
            for seg in (segments or []) for w in (seg.get("words") or [])]


def _spans_from_params(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rebuild the clipper's span dicts from a job's `clips` + `segments`
    params — the storage-backed equivalent of ClipRequest handling."""
    all_words = _flatten_words(params.get("segments") or [])

    def words_in(a: float, b: float) -> list:
        return [w for w in all_words if w["start"] < b and w["end"] > a]

    spans = []
    for c in params.get("clips") or []:
        start, end = float(c["start"]), float(c["end"])
        if end <= start:
            continue
        spans.append({
            "start": start, "end": end, "text": c.get("text", ""),
            "words": words_in(start, end),
            "music_b64": c.get("music_b64"),
            "music_volume": c.get("music_volume", 0.35),
        })
    return spans


# ── transcribe ─────────────────────────────────────────────────────────────
def _run_transcribe(path: str, model_name: str) -> Dict[str, Any]:
    """Pick the transcription backend: faster-whisper (CPU/Linux, e.g. the HF
    Space) or MLX (Apple dev). `TLDW_WHISPER_BACKEND` forces one; default auto
    prefers faster-whisper and falls back to the MLX pipeline."""
    backend = os.environ.get("TLDW_WHISPER_BACKEND", "auto")
    if backend in ("auto", "faster"):
        try:
            from .cpu_backends import faster_whisper_transcribe
            return faster_whisper_transcribe(path, model_name)
        except ImportError:
            if backend == "faster":
                raise
    # MLX fallback (Apple Silicon dev).
    return _load("transcribe_pipeline", POC_AUDIO_DIR).transcribe(path, model_name=model_name)


def transcribe_handler(ctx: JobContext) -> Result:
    ctx.progress(0.05, "fetching media")
    path = ctx.input_path()
    ctx.progress(0.15, "loading transcriber")
    model_name = (ctx.params.get("model") or WHISPER_MODEL).strip()
    ctx.progress(0.25, "transcribing")
    result = _run_transcribe(path, model_name)
    ctx.progress(0.95, "done")
    return Result(data={
        "source": os.path.basename(path),
        "duration_s": result["duration_s"],
        "speech_ratio": result["speech_ratio"],
        "model": result["model"],
        "text": result["text"],
        "segments": result["segments"],
    })


# ── bloopers ───────────────────────────────────────────────────────────────
def bloopers_handler(ctx: JobContext) -> Result:
    ctx.progress(0.05, "fetching media")
    path = ctx.input_path()
    ctx.progress(0.2, "loading detector")
    mod = _load("blooper", POC_BLOOPER_DIR)
    use_lip_check = bool(ctx.params.get("use_lip_check", True))
    ctx.progress(0.3, "detecting non-speech spans")
    spans = mod.detect(path, min_dur=0.0, use_lip_check=use_lip_check)
    duration = mod.video_duration(path)
    ctx.progress(0.95, "done")
    return Result(data={
        "source": os.path.basename(path),
        "duration_s": round(duration, 3),
        "count": len(spans),
        "params": {"lip_check": use_lip_check},
        "bloopers": [
            {"index": i, "start": b.start, "end": b.end, "duration": b.duration,
             "lip_motion": b.lip_motion, "label": b.label}
            for i, b in enumerate(spans)
        ],
    })


# ── clip (multi-output) ────────────────────────────────────────────────────
def clip_handler(ctx: JobContext) -> Result:
    ctx.progress(0.05, "fetching media")
    path = ctx.input_path()
    spans = _spans_from_params(ctx.params)
    if not spans:
        raise ValueError("No valid clip spans to cut.")

    mod = _load("cut_viral_clips", POC_CLIPPER_DIR)
    out_dir = os.path.join(ctx.workdir, "clips")
    vertical = bool(ctx.params.get("vertical", True))
    want_subs = bool(ctx.params.get("subtitles", True))
    want_srt = bool(ctx.params.get("srt", True))
    subtitles_applied = want_subs and mod.ffmpeg_has_subtitles()

    ctx.progress(0.2, f"cutting {len(spans)} clip(s)")
    written = mod.cut_clips(path, spans, out_dir, vertical=vertical, subtitles=want_subs)

    # Optional .srt sidecars (non-fatal), mirroring server.py.
    srt_for: Dict[str, str] = {}
    if want_srt:
        try:
            subs = _load("subtitles", POC_SUBTITLES_DIR)
            for p, s in zip(written, spans):
                clip_words = subs.rebase_words(s["words"], s["start"], s["end"])
                cues = subs.build_cues(clip_words) if clip_words else []
                if not cues:
                    continue
                srt_path = os.path.splitext(p)[0] + ".srt"
                subs.write_srt(cues, srt_path)
                srt_for[p] = srt_path
        except Exception as exc:  # keep the clips; just skip captions
            print(f"[cloud] .srt sidecar generation failed: {exc}")

    outputs: List[Dict[str, Any]] = []
    clips_meta: List[Dict[str, Any]] = []
    for i, (p, s) in enumerate(zip(written, spans)):
        ctx.progress(0.5 + 0.45 * (i + 1) / len(written), f"uploading clip {i + 1}/{len(written)}")
        out = ctx.put_output(p, kind="clip", name=f"clip_{i:02d}{os.path.splitext(p)[1]}")
        meta = {"index": i, "key": out["key"], "start": s["start"], "end": s["end"], "text": s["text"]}
        if p in srt_for:
            srt_out = ctx.put_output(srt_for[p], kind="srt", name=f"clip_{i:02d}.srt")
            outputs.append(srt_out)
            meta["srt_key"] = srt_out["key"]
        outputs.append(out)
        clips_meta.append(meta)

    return Result(
        data={
            "source": os.path.basename(path),
            "count": len(written),
            "vertical": vertical,
            "subtitles_applied": subtitles_applied,
            "subtitles_requested": want_subs,
            "srt_count": len(srt_for),
            "clips": clips_meta,
        },
        outputs=outputs,
    )


# ── merge (single output) ──────────────────────────────────────────────────
def merge_handler(ctx: JobContext) -> Result:
    ctx.progress(0.05, "fetching media")
    path = ctx.input_path()
    spans = _spans_from_params(ctx.params)
    if not spans:
        raise ValueError("No valid clip spans to merge.")

    mod = _load("cut_viral_clips", POC_CLIPPER_DIR)
    vertical = bool(ctx.params.get("vertical", True))
    want_subs = bool(ctx.params.get("subtitles", True))
    subtitles_applied = want_subs and mod.ffmpeg_has_subtitles()

    name = (ctx.params.get("name") or "merged").strip() or "merged"
    out_path = os.path.join(ctx.workdir, f"{name}.mp4")

    # Timeline-placed music beds (server.py MergeRequest.music).
    music = [{"b64": mp["b64"], "start": mp.get("start", 0.0),
              "duration": mp["duration"], "volume": mp.get("volume", 0.35)}
             for mp in (ctx.params.get("music") or [])
             if mp.get("b64") and mp.get("duration", 0) > 0] or None

    ctx.progress(0.2, f"merging {len(spans)} span(s)")
    written = mod.merge_clips(path, spans, out_path, vertical=vertical,
                              subtitles=want_subs, music=music)
    ctx.progress(0.8, "uploading merged video")
    out = ctx.put_output(written, kind="merge", name=f"{name}.mp4")

    return Result(
        data={
            "source": os.path.basename(path),
            "clip_count": len(spans),
            "vertical": vertical,
            "subtitles_applied": subtitles_applied,
            "subtitles_requested": want_subs,
        },
        outputs=[out],
    )


register("transcribe", transcribe_handler)
register("bloopers", bloopers_handler)
register("clip", clip_handler)
register("merge", merge_handler)
