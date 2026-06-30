"""tldw — Transcript Content Highlighter sidecar.

A FastAPI server that runs the 5-step highlighter pipeline and returns two
top-K ranked lists for a transcript:

    1. Summarize the transcript                 google/pegasus-cnn_dailymail
    2. Embed each line + the summary            sentence-transformers/all-mpnet-base-v2
    3. relevance = cosine(line, summary)        (most-relevant list)
    4. Virality Detector (classification)       distilbert  -> viral_prob / viral_label
    5. Virality Reranker (regression)           bert-base   -> viral_score (most-viral list)

The SwiftUI app talks to this over http://127.0.0.1:8000.

Run:  ./setup.sh && ./run.sh
"""
from __future__ import annotations

import base64
import io
import os
import re
import sys
import wave
from typing import Optional

import numpy as np
import torch
import torch.nn.functional as F
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                          AutoModelForSeq2SeqLM, AutoProcessor,
                          MusicgenForConditionalGeneration)

# ── Config ────────────────────────────────────────────────────────────────
DEVICE = ("cuda" if torch.cuda.is_available()
          else "mps" if torch.backends.mps.is_available() else "cpu")

MODELS_DIR = os.environ.get(
    "TLDW_MODELS_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "poc-content-highlighter", "models"),
)
SUMMARIZER_NAME = "google/pegasus-cnn_dailymail"
EMBEDDER_NAME   = "sentence-transformers/all-mpnet-base-v2"
DETECTOR_DIR    = os.path.join(MODELS_DIR, "distilbert-detector")
RERANKER_DIR    = os.path.join(MODELS_DIR, "bert-ranker")

SUMM_MAX_INPUT  = 1024  # Pegasus encoder cap (max_position_embeddings)
SCORE_MAX_LEN   = 64    # detector/reranker were trained at max_length=64
MIN_LINE_WORDS  = 4     # drop trivially short lines

# Backsound: sentiment -> valence/arousal -> MusicGen prompt -> music bed.
EMOTION_NAME      = "j-hartmann/emotion-english-distilroberta-base"
MUSICGEN_NAME     = os.environ.get("TLDW_MUSICGEN", "facebook/musicgen-small")
MUSIC_DEVICE      = os.environ.get("TLDW_MUSIC_DEVICE", DEVICE)  # set =cpu if MPS misbehaves
MUSIC_MAX_SECONDS = 15.0
MUSICGEN_TOK_RATE = 50                   # MusicGen emits ~50 audio tokens / second

# Bloopers: Silero VAD inverts speech → non-speech ("blooper") spans, with an
# optional OpenCV lip-motion check. The pipeline lives in the PoC module.
POC_BLOOPER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "poc-blooper-detector")

# ── Lazy-loaded model singletons ─────────────────────────────────────────
_models = {}


def _load_models():
    if _models:
        return _models
    print(f"[tldw] loading models on {DEVICE} …")
    _models["summarizer_tok"] = AutoTokenizer.from_pretrained(SUMMARIZER_NAME)
    _models["summarizer"] = AutoModelForSeq2SeqLM.from_pretrained(SUMMARIZER_NAME).to(DEVICE).eval()
    _models["embedder"] = SentenceTransformer(EMBEDDER_NAME, device=DEVICE)

    _models["detector_tok"] = AutoTokenizer.from_pretrained(DETECTOR_DIR)
    _models["detector"] = AutoModelForSequenceClassification.from_pretrained(DETECTOR_DIR).to(DEVICE).eval()

    _models["reranker_tok"] = AutoTokenizer.from_pretrained(RERANKER_DIR)
    _models["reranker"] = AutoModelForSequenceClassification.from_pretrained(RERANKER_DIR).to(DEVICE).eval()
    print("[tldw] models ready")
    return _models


# ── Pipeline pieces ────────────────────────────────────────────────────────
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_lines(text: str) -> list[str]:
    """Break a transcript into candidate lines: explicit line breaks first,
    otherwise sentence splits. Drop trivially short fragments."""
    raw = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(raw) <= 1:  # no real line breaks -> split into sentences
        raw = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    return [ln for ln in raw if len(ln.split()) >= MIN_LINE_WORDS]


def _sentence_chunks(text: str, tok, budget: int) -> list[str]:
    """Pack whole sentences into chunks of <= budget content-tokens (greedy),
    so no sentence is ever split across a chunk boundary."""
    sents = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]

    chunks, cur, cur_len = [], [], 0
    for s in sents:
        n = len(tok(s, add_special_tokens=False)["input_ids"])
        # A single sentence longer than the budget can't fit whole anywhere;
        # hard-split just that one by tokens (unavoidable mid-sentence cut).
        if n > budget:
            if cur:
                chunks.append(" ".join(cur)); cur, cur_len = [], 0
            ids = tok(s, add_special_tokens=False)["input_ids"]
            for i in range(0, len(ids), budget):
                chunks.append(tok.decode(ids[i:i + budget], skip_special_tokens=True))
            continue
        # Flush before adding the sentence that would overflow the budget.
        if cur_len + n > budget:
            chunks.append(" ".join(cur)); cur, cur_len = [], 0
        cur.append(s); cur_len += n

    if cur:
        chunks.append(" ".join(cur))
    return chunks


@torch.no_grad()
def summarize(text: str, m) -> str:
    """Summarize the transcript; hierarchically condense long inputs so we stay
    under Pegasus's encoder cap."""
    tok, model = m["summarizer_tok"], m["summarizer"]

    def _one(chunk: str) -> str:
        enc = tok(chunk, return_tensors="pt", truncation=True,
                  max_length=SUMM_MAX_INPUT).to(DEVICE)
        out = model.generate(**enc, max_length=130, min_length=30, num_beams=4)
        return tok.decode(out[0], skip_special_tokens=True).replace("<n>", " ").strip()

    ids = tok(text, return_tensors="pt")["input_ids"][0]
    if len(ids) <= SUMM_MAX_INPUT:
        return _one(text)
    # Map: sentence-aware chunks, summarize each. Reduce: summarize the joined
    # partials, re-chunking if they themselves exceed the encoder cap.
    partial = " ".join(_one(c) for c in _sentence_chunks(text, tok, SUMM_MAX_INPUT))
    while len(tok(partial, return_tensors="pt")["input_ids"][0]) > SUMM_MAX_INPUT:
        partial = " ".join(_one(c) for c in _sentence_chunks(partial, tok, SUMM_MAX_INPUT))
    return _one(partial)


@torch.no_grad()
def relevance_scores(lines: list[str], summary: str, m) -> np.ndarray:
    emb = m["embedder"].encode([summary] + lines, normalize_embeddings=True,
                               convert_to_numpy=True)
    summary_vec, line_vecs = emb[0], emb[1:]
    return line_vecs @ summary_vec  # cosine (vectors are normalized)


@torch.no_grad()
def detector_scores(lines: list[str], m) -> tuple[np.ndarray, np.ndarray]:
    tok, model = m["detector_tok"], m["detector"]
    # DistilBERT's forward() has no `token_type_ids` arg, so don't emit them.
    enc = tok(lines, return_tensors="pt", truncation=True, padding=True,
              max_length=SCORE_MAX_LEN, return_token_type_ids=False).to(DEVICE)
    logits = model(**enc).logits
    probs = F.softmax(logits, dim=-1)[:, 1]
    labels = logits.argmax(-1)
    return probs.cpu().numpy(), labels.cpu().numpy()


@torch.no_grad()
def reranker_scores(lines: list[str], m) -> np.ndarray:
    tok, model = m["reranker_tok"], m["reranker"]
    enc = tok(lines, return_tensors="pt", truncation=True, padding=True,
              max_length=SCORE_MAX_LEN).to(DEVICE)
    return model(**enc).logits.squeeze(-1).cpu().numpy()


# ── Audio helpers (shared by the backsound endpoint) ───────────────────────
def _estimate_seconds(text: str) -> float:
    """Spoken duration at ~2.5 words/sec — matches the SwiftUI card estimate."""
    return max(1.0, round(len(text.split()) / 2.5))


def _to_wav_bytes(signal: np.ndarray, sr: int) -> bytes:
    """Mono float[-1,1] -> 16-bit PCM WAV bytes."""
    pcm = (np.clip(signal, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


# ── Backsound (sentiment -> MusicGen music bed) ────────────────────────────
# Each emotion sits at a (valence, arousal) point in [-1, 1] — Russell's
# circumplex. We take the softmax-weighted average, then translate that point
# into musical adjectives/mode/tempo for the MusicGen prompt.
_EMOTION_VA = {
    "anger":    (-0.6,  0.8),
    "disgust":  (-0.6,  0.4),
    "fear":     (-0.7,  0.8),
    "joy":      ( 0.8,  0.6),
    "neutral":  ( 0.0,  0.0),
    "sadness":  (-0.7, -0.4),
    "surprise": ( 0.4,  0.7),
}

# Lazily loaded so the heavy MusicGen weights download only on first /backsound.
_music = {}


def _load_music():
    if _music:
        return _music
    print(f"[tldw] loading emotion + MusicGen ({MUSICGEN_NAME}) on {MUSIC_DEVICE} …")
    _music["emo_tok"] = AutoTokenizer.from_pretrained(EMOTION_NAME)
    _music["emo"] = AutoModelForSequenceClassification.from_pretrained(EMOTION_NAME).to(MUSIC_DEVICE).eval()
    _music["proc"] = AutoProcessor.from_pretrained(MUSICGEN_NAME)
    _music["gen"] = MusicgenForConditionalGeneration.from_pretrained(MUSICGEN_NAME).to(MUSIC_DEVICE).eval()
    print("[tldw] MusicGen ready")
    return _music


@torch.no_grad()
def _emotion_va(text: str, m) -> tuple[float, float, str, dict]:
    """Classify emotion and reduce to a (valence, arousal) point."""
    tok, model = m["emo_tok"], m["emo"]
    enc = tok(text, return_tensors="pt", truncation=True, max_length=128).to(MUSIC_DEVICE)
    probs = F.softmax(model(**enc).logits, dim=-1)[0].cpu().numpy()
    id2label = model.config.id2label

    val = aro = 0.0
    scores = {}
    for i, p in enumerate(probs):
        label = id2label[i].lower()
        v, a = _EMOTION_VA.get(label, (0.0, 0.0))
        val += p * v
        aro += p * a
        scores[label] = round(float(p), 3)
    dominant = id2label[int(probs.argmax())].lower()
    return float(val), float(aro), dominant, scores


def _va_to_music_prompt(valence: float, arousal: float) -> str:
    """Map a (valence, arousal) point to a MusicGen text prompt."""
    mode = ("major key" if valence >= 0.15
            else "minor key" if valence <= -0.15 else "modal")
    if arousal >= 0.5:
        tempo = "energetic and driving, around 120 BPM"
    elif arousal <= -0.1:
        tempo = "slow and sparse, around 60 BPM, ambient"
    else:
        tempo = "moderate tempo, around 90 BPM"
    # Quadrant adjectives (valence × arousal), with a neutral dead-zone center.
    if abs(valence) < 0.15 and abs(arousal) < 0.2:
        mood = "calm, neutral, understated"
    elif valence >= 0 and arousal >= 0:
        mood = "bright, uplifting, hopeful"
    elif valence >= 0:
        mood = "warm, gentle, peaceful"
    elif arousal >= 0:
        mood = "dark, tense, suspenseful, dissonant"
    else:
        mood = "somber, melancholic, hollow"
    return (f"{mood} instrumental background music, {mode}, {tempo}, "
            f"cinematic underscore, no vocals")


def _generate_music(prompt: str, seconds: float, m) -> tuple[bytes, int]:
    """Run MusicGen and return (WAV bytes, sample_rate)."""
    proc, gen = m["proc"], m["gen"]
    inputs = proc(text=[prompt], padding=True, return_tensors="pt").to(MUSIC_DEVICE)
    sr = gen.config.audio_encoder.sampling_rate
    max_new = int(seconds * MUSICGEN_TOK_RATE)
    with torch.no_grad():
        audio = gen.generate(**inputs, do_sample=True, guidance_scale=3.0,
                             max_new_tokens=max_new)
    wav = audio[0, 0].cpu().numpy().astype("float32")     # mono float [-1, 1]
    wav = wav / (np.max(np.abs(wav)) + 1e-9) * 0.9        # normalize headroom
    return _to_wav_bytes(wav, sr), sr


# ── Bloopers (Silero VAD → non-speech spans) ───────────────────────────────
# The detection pipeline lives in poc-blooper-detector/blooper.py. We import it
# lazily and cache the module: its top level only needs numpy, but Silero VAD
# (torch.hub) and OpenCV are pulled in on the first detect() call, so /highlight
# startup is unaffected.
_blooper = {}


def _load_blooper():
    if "mod" not in _blooper:
        if POC_BLOOPER_DIR not in sys.path:
            sys.path.insert(0, POC_BLOOPER_DIR)
        import blooper  # noqa: E402  (intentionally deferred)
        _blooper["mod"] = blooper
    return _blooper["mod"]


# ── API ──────────────────────────────────────────────────────────────────
app = FastAPI(title="tldw highlighter")


class HighlightRequest(BaseModel):
    text: str
    top_k: int = 10


class BacksoundRequest(BaseModel):
    text: str                             # line (or whole-Short text) to read mood from
    duration_s: Optional[int] = None      # bed length; defaults to a spoken estimate


class BlooperRequest(BaseModel):
    video_path: str                       # absolute path to the source video (local)
    use_lip_check: bool = True            # confirm a still mouth with the visual check


@app.get("/health")
def health():
    return {"status": "ok", "device": DEVICE}


@app.post("/highlight")
def highlight(req: HighlightRequest):
    text = req.text.strip()
    if len(text) < 40:
        raise HTTPException(status_code=400, detail="Transcript too short (need ~40+ characters).")

    lines = split_lines(text)
    if not lines:
        raise HTTPException(status_code=400, detail="Could not extract any usable lines from the transcript.")

    m = _load_models()
    summary = summarize(text, m)

    relevance = relevance_scores(lines, summary, m)
    viral_prob, viral_label = detector_scores(lines, m)
    viral_score = reranker_scores(lines, m)

    def row(i: int) -> dict:
        i = int(i)  # argsort yields np.int64; cast for JSON serialization
        return {"index": i, "text": lines[i],
                "relevance": round(float(relevance[i]), 4),
                "viral_score": round(float(viral_score[i]), 4),
                "viral_prob": round(float(viral_prob[i]), 4),
                "viral_label": bool(viral_label[i])}

    k = max(1, min(req.top_k, len(lines)))
    # Relevant list: all lines ranked by similarity to the summary.
    relevant = [row(i) for i in np.argsort(-relevance)[:k]]
    # Viral list: cascade — the detector first gates out non-viral lines, then
    # the reranker scores/orders only the survivors.
    viral_ranked = [i for i in np.argsort(-viral_score) if viral_label[i] == 1]
    viral = [row(i) for i in viral_ranked[:k]]

    return {
        "summary": summary,
        "models": {"summarizer": SUMMARIZER_NAME, "embedder": EMBEDDER_NAME,
                   "detector": "distilbert-detector", "reranker": "bert-ranker"},
        "line_count": len(lines),
        "relevant": relevant,
        "viral": viral,
    }


@app.post("/backsound")
def backsound(req: BacksoundRequest):
    """Generate an emotion-matched background music bed for a clip (editor)."""
    text = req.text.strip()
    if len(text) < 4:
        raise HTTPException(status_code=400, detail="Text too short to read a mood from.")

    seconds = float(req.duration_s) if req.duration_s else _estimate_seconds(text)
    seconds = max(2.0, min(seconds, MUSIC_MAX_SECONDS))

    m = _load_music()
    valence, arousal, emotion, scores = _emotion_va(text, m)
    prompt = _va_to_music_prompt(valence, arousal)
    wav, sr = _generate_music(prompt, seconds, m)

    return {
        "prompt": prompt,
        "emotion": emotion,
        "valence": round(valence, 3),
        "arousal": round(arousal, 3),
        "scores": scores,
        "audio_b64": base64.b64encode(wav).decode("ascii"),
        "sample_rate": sr,
        "duration_s": round(seconds, 2),
        "model": MUSICGEN_NAME,
    }


@app.post("/bloopers")
def bloopers(req: BlooperRequest):
    """Find non-speech 'blooper' spans (silence / pauses / dead air) in a video.

    The macOS app picks the source video and sends its local path; the app then
    previews each span by seeking the source video, so we only return span
    metadata here (no clip cutting)."""
    path = os.path.expanduser(req.video_path.strip())
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=400, detail=f"Video not found: {req.video_path}")

    mod = _load_blooper()
    try:
        # min_dur=0: any non-speech span counts as a blooper, no duration floor.
        # (merge_gap / pad stay at the module defaults — VAD-flicker smoothing and
        # edge padding so we don't fragment one pause or clip the surrounding words.)
        spans = mod.detect(
            path,
            min_dur=0.0,
            use_lip_check=req.use_lip_check,
        )
        duration = mod.video_duration(path)
    except Exception as exc:  # ffmpeg / VAD / decode failures → 500 with the reason
        raise HTTPException(status_code=500, detail=f"Blooper detection failed: {exc}")

    return {
        "source": os.path.basename(path),
        "duration_s": round(duration, 3),
        "count": len(spans),
        "params": {"lip_check": req.use_lip_check},
        "bloopers": [
            {
                "index": i,
                "start": b.start,
                "end": b.end,
                "duration": b.duration,
                "lip_motion": b.lip_motion,
                "label": b.label,
            }
            for i, b in enumerate(spans)
        ],
    }
