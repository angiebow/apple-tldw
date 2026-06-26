"""Blooper (non-speech) detector.

Takes a podcast/newscast video and finds every span where the speaker(s) are
not talking -- silence, pauses, music stings, cutaways, dropped mics -- then
cuts each span out as its own clip.

Pipeline:
    extract_audio  -> 16 kHz mono WAV (ffmpeg)
    speech_spans   -> Silero VAD speech timestamps
    nonspeech_spans-> invert speech -> candidate bloopers (interval math)
    lip_motion_score-> MediaPipe FaceMesh, confirm lips are static (optional)
    detect         -> orchestrates the above into a list of Blooper
    cut_clips      -> one ffmpeg-cut .mp4 per blooper + bloopers.json

The visual lip check is best-effort: if MediaPipe is unavailable or no face is
found, the audio-only verdict stands (label "no_face").
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

Span = Tuple[float, float]


# --------------------------------------------------------------------------- #
# 0. helpers
# --------------------------------------------------------------------------- #
def _require_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install it first: `brew install ffmpeg`."
        )
    return exe


def video_duration(video: str) -> float:
    """Total duration of a media file in seconds, via ffprobe."""
    probe = shutil.which("ffprobe")
    if not probe:
        raise RuntimeError("ffprobe not found (ships with ffmpeg).")
    out = subprocess.run(
        [probe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


# --------------------------------------------------------------------------- #
# 1. audio extraction
# --------------------------------------------------------------------------- #
def extract_audio(video: str, out_wav: Optional[str] = None) -> str:
    """Decode the video's audio to a 16 kHz mono WAV (what Silero VAD wants)."""
    ffmpeg = _require_ffmpeg()
    if out_wav is None:
        out_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    subprocess.run(
        [ffmpeg, "-y", "-i", str(video),
         "-ac", "1", "-ar", "16000", "-vn", "-f", "wav", str(out_wav)],
        capture_output=True, check=True,
    )
    return out_wav


# --------------------------------------------------------------------------- #
# 2. speech detection (Silero VAD)
# --------------------------------------------------------------------------- #
_VAD_CACHE: dict = {}


def _load_vad():
    """Lazily load Silero VAD from torch.hub (cached after first call)."""
    if "model" not in _VAD_CACHE:
        import torch  # imported lazily so the module loads without torch present

        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
        get_ts = utils[0]  # get_speech_timestamps
        _VAD_CACHE.update(model=model, get_ts=get_ts, torch=torch)
    return _VAD_CACHE


def _read_wav_mono(wav: str, sampling_rate: int = 16000):
    """Load a WAV as a 1-D float32 torch tensor, via soundfile.

    We deliberately avoid silero's `read_audio` / `torchaudio.load`: torchaudio
    >= 2.9 routes I/O through the separate `torchcodec` package, which isn't
    always installed. `extract_audio` already produced 16 kHz mono PCM, so a
    plain soundfile read is all we need.
    """
    import soundfile as sf

    torch = _load_vad()["torch"]
    data, sr = sf.read(str(wav), dtype="float32")
    if data.ndim > 1:               # guard: collapse to mono if ever stereo
        data = data.mean(axis=1)
    if sr != sampling_rate:
        raise ValueError(
            f"expected {sampling_rate} Hz mono WAV from extract_audio, got {sr} Hz"
        )
    return torch.from_numpy(data)


def speech_spans(wav: str, sampling_rate: int = 16000) -> List[Span]:
    """Speech intervals (seconds) detected by Silero VAD."""
    vad = _load_vad()
    audio = _read_wav_mono(wav, sampling_rate)
    ts = vad["get_ts"](audio, vad["model"], sampling_rate=sampling_rate)
    return [(t["start"] / sampling_rate, t["end"] / sampling_rate) for t in ts]


# --------------------------------------------------------------------------- #
# 3. invert speech -> candidate non-speech spans (pure interval math)
# --------------------------------------------------------------------------- #
def nonspeech_spans(
    speech: List[Span],
    total_dur: float,
    min_dur: float = 0.8,
    pad: float = 0.1,
    merge_gap: float = 0.3,
) -> List[Span]:
    """Complement of the speech intervals over [0, total_dur].

    - merge speech intervals separated by < merge_gap (so tiny VAD flickers
      don't fragment one pause into many),
    - take the gaps between merged speech,
    - trim `pad` off each gap end (don't clip the surrounding words),
    - drop gaps shorter than `min_dur`.
    """
    speech = sorted(speech)

    # merge near-adjacent speech intervals
    merged: List[Span] = []
    for s, e in speech:
        if merged and s - merged[-1][1] < merge_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # gaps = complement of merged speech over the timeline
    gaps: List[Span] = []
    prev = 0.0
    for s, e in merged:
        if s > prev:
            gaps.append((prev, s))
        prev = max(prev, e)
    if prev < total_dur:
        gaps.append((prev, total_dur))

    out: List[Span] = []
    for s, e in gaps:
        s2, e2 = s + pad, e - pad
        if e2 - s2 >= min_dur:
            out.append((round(s2, 3), round(e2, 3)))
    return out


# --------------------------------------------------------------------------- #
# 4. visual lip-motion check (OpenCV Haar face + mouth frame-diff) -- best effort
# --------------------------------------------------------------------------- #
# Why OpenCV and not MediaPipe: MediaPipe doesn't ship working wheels for every
# macOS-arm64 / Python combo (the installed package is often a stub missing the
# `solutions` API). OpenCV's Haar cascade ships with opencv-python and works
# everywhere cv2 imports. We detect the face, crop the mouth region (lower part
# of the face box) and measure how much it changes frame-to-frame -- a moving
# mouth produces large inter-frame differences, a still mouth produces small
# ones. Cruder than lip landmarks but no extra dependency.
_MOUTH_ROI = 48  # mouth crop is resized to this square before differencing


def _face_cascade(cv2):
    if "cascade" not in _VAD_CACHE:
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _VAD_CACHE["cascade"] = cv2.CascadeClassifier(path)
    return _VAD_CACHE["cascade"]


def lip_motion_score(
    video: str,
    start: float,
    end: float,
    fps_sample: float = 5.0,
    max_frames: int = 40,
) -> Optional[float]:
    """Mean inter-frame change of the mouth region; None if no face / no cv2.

    Low score  -> mouth static  -> genuinely not speaking.
    High score -> mouth moving  -> speaker mouthing while audio is silent.
    Score is the mean absolute frame-to-frame difference of a normalised
    grayscale mouth crop, in [0, 1] (so it's exposure/scale-tolerant).
    """
    try:
        import cv2
    except Exception:
        return None

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return None

    cascade = _face_cascade(cv2)
    n = max(2, min(max_frames, int((end - start) * fps_sample)))
    times = np.linspace(start, end, n)
    mouths: List[np.ndarray] = []

    try:
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                             minSize=(60, 60))
            if len(faces) == 0:
                continue
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])  # largest face
            # mouth ~ lower third of the face box, central 60% of its width
            my0, my1 = y + int(0.62 * h), y + h
            mx0, mx1 = x + int(0.20 * w), x + int(0.80 * w)
            roi = gray[my0:my1, mx0:mx1]
            if roi.size == 0:
                continue
            roi = cv2.resize(roi, (_MOUTH_ROI, _MOUTH_ROI)).astype(np.float32) / 255.0
            mouths.append(roi)
    finally:
        cap.release()

    if len(mouths) < 2:
        return None
    diffs = [float(np.mean(np.abs(mouths[i] - mouths[i - 1])))
             for i in range(1, len(mouths))]
    return float(np.mean(diffs))


# --------------------------------------------------------------------------- #
# 5. orchestration
# --------------------------------------------------------------------------- #
@dataclass
class Blooper:
    start: float
    end: float
    duration: float
    lip_motion: Optional[float]
    label: str  # "silent" | "lips_moving" | "no_face"


def _label(lip_motion: Optional[float], lip_threshold: float) -> str:
    if lip_motion is None:
        return "no_face"
    return "lips_moving" if lip_motion > lip_threshold else "silent"


def detect(
    video: str,
    min_dur: float = 0.8,
    pad: float = 0.1,
    merge_gap: float = 0.3,
    lip_threshold: float = 0.04,
    use_lip_check: bool = True,
) -> List[Blooper]:
    """Full pipeline: video -> list of Blooper spans."""
    wav = extract_audio(video)
    try:
        spans = speech_spans(wav)
    finally:
        Path(wav).unlink(missing_ok=True)

    dur = video_duration(video)
    gaps = nonspeech_spans(spans, dur, min_dur=min_dur, pad=pad, merge_gap=merge_gap)

    bloopers: List[Blooper] = []
    for s, e in gaps:
        lm = lip_motion_score(video, s, e) if use_lip_check else None
        bloopers.append(
            Blooper(start=s, end=e, duration=round(e - s, 3),
                    lip_motion=lm, label=_label(lm, lip_threshold))
        )
    return bloopers


# --------------------------------------------------------------------------- #
# 6. cut clips + write index
# --------------------------------------------------------------------------- #
def cut_clips(
    video: str,
    bloopers: List[Blooper],
    outdir: str = "out",
    params: Optional[dict] = None,
) -> dict:
    """Cut one frame-accurate .mp4 per blooper and write out/bloopers.json."""
    ffmpeg = _require_ffmpeg()
    out = Path(outdir)
    clips = out / "clips"
    clips.mkdir(parents=True, exist_ok=True)

    records = []
    for i, b in enumerate(bloopers):
        name = f"blooper_{i:03d}_{b.start:.1f}-{b.end:.1f}.mp4"
        path = clips / name
        # -ss/-to after -i = frame-accurate (decodes then trims); re-encode.
        subprocess.run(
            [ffmpeg, "-y", "-i", str(video),
             "-ss", f"{b.start}", "-to", f"{b.end}",
             "-c:v", "libx264", "-c:a", "aac", str(path)],
            capture_output=True, check=True,
        )
        rec = asdict(b)
        rec["clip"] = f"clips/{name}"
        records.append(rec)

    index = {
        "source": str(Path(video).name),
        "params": params or {},
        "count": len(records),
        "bloopers": records,
    }
    (out / "bloopers.json").write_text(json.dumps(index, indent=2))
    return index


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Extract non-speech 'blooper' clips from a video.")
    ap.add_argument("video", help="path to podcast/newscast video")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--min-dur", type=float, default=0.8)
    ap.add_argument("--no-lip-check", action="store_true")
    args = ap.parse_args()

    bl = detect(args.video, min_dur=args.min_dur, use_lip_check=not args.no_lip_check)
    print(f"Detected {len(bl)} blooper span(s):")
    for b in bl:
        print(f"  {b.start:7.2f} -> {b.end:7.2f}  ({b.duration:5.2f}s)  {b.label}")
    cut_clips(args.video, bl, outdir=args.outdir,
              params={"min_dur": args.min_dur, "lip_check": not args.no_lip_check})
    print(f"Wrote clips + index to {args.outdir}/")
