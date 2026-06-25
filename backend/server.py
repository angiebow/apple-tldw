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
import hashlib
import io
import os
import re
import wave
from typing import Optional

import numpy as np
import torch
import torch.nn.functional as F
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                          AutoModelForSeq2SeqLM)

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

SFX_SAMPLE_RATE = 32000                 # Stable Audio Open native rate
SFX_MODEL_NAME  = "stub:procedural-v0"  # TODO: stabilityai/stable-audio-open-1.0
SFX_MAX_SECONDS = 12.0                  # keep one-shot SFX short

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


# ── Sound effects (per selected clip) ──────────────────────────────────────
# Rough keyword -> acoustic-mood map for the stub prompt builder. The real
# version hands the line to an LLM (e.g. Claude Haiku) and asks for a short SFX
# prompt + a hit time; this heuristic keeps the endpoint dependency-free for now.
_SFX_CUES = [
    (("scream", "horror", "fear", "terror", "hell", "pain", "nightmare"),
     "tense low drone rising into a sharp metallic stinger"),
    (("laugh", "funny", "joke", "lol", "hilarious"),
     "light comedic pop with a quick rimshot"),
    (("explode", "boom", "blast", "war", "fight", "destroy", "crash"),
     "deep impact boom with a debris tail"),
    (("money", "win", "reward", "success", "rich"),
     "bright celebratory chime with a shimmer"),
    (("run", "fast", "chase", "rush", "race"),
     "fast whoosh sweeping past"),
    (("sad", "cry", "alone", "lost", "die", "death", "grief"),
     "somber hollow drone fading slowly"),
]
_DEFAULT_CUE = "subtle cinematic whoosh leading into a soft impact"


def _estimate_seconds(text: str) -> float:
    """Spoken duration at ~2.5 words/sec — matches the SwiftUI card estimate."""
    return max(1.0, round(len(text.split()) / 2.5))


def _derive_sfx_prompt(text: str, viral_score: float | None) -> str:
    """Turn a line into a short text-to-audio prompt. LLM seam: swap this body
    for a Claude Haiku call that reads the line and emits {prompt, hit_time}."""
    low = text.lower()
    cue = next((p for keys, p in _SFX_CUES if any(k in low for k in keys)), _DEFAULT_CUE)
    intensity = "punchy, loud, foreground" if (viral_score or 0) >= 0.5 else "subtle, background"
    return f"{cue}; {intensity}"


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


def _synthesize_placeholder(prompt: str, seconds: float, sr: int) -> np.ndarray:
    """Deterministic procedural SFX (filtered noise + falling sweep under an
    attack/decay envelope) so the audio path is testable without a model."""
    seed = int.from_bytes(hashlib.sha256(prompt.encode()).digest()[:4], "big")
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.linspace(0, seconds, n, endpoint=False)
    sweep = np.sin(2 * np.pi * (400 * np.exp(-3 * t)) * t)        # falling pitch
    noise = rng.standard_normal(n)
    env = np.exp(-3 * t) * (1 - np.exp(-40 * t))                  # fast attack, decay
    sig = env * (0.6 * sweep + 0.4 * noise)
    return sig / (np.max(np.abs(sig)) + 1e-9) * 0.9              # normalize


def _generate_sfx(prompt: str, seconds: float) -> bytes:
    """SFX generation seam — returns WAV bytes.

    Production path (TODO): load stabilityai/stable-audio-open-1.0 once in
    _load_models(), then generate timed audio:
        audio = pipe(prompt, audio_end_in_s=seconds).audios[0]  # [-1,1] float
        return _to_wav_bytes(audio, SFX_SAMPLE_RATE)
    For now we synthesize a procedural placeholder so the editor's full path
    (request -> base64 WAV -> AVAudioPlayer) works end to end.
    """
    signal = _synthesize_placeholder(prompt, seconds, SFX_SAMPLE_RATE)
    return _to_wav_bytes(signal, SFX_SAMPLE_RATE)


# ── API ──────────────────────────────────────────────────────────────────
app = FastAPI(title="tldw highlighter")


class HighlightRequest(BaseModel):
    text: str
    top_k: int = 10


class SFXRequest(BaseModel):
    text: str
    viral_score: Optional[float] = None   # from the ranked line; nudges intensity
    duration_s: Optional[int] = None      # clip length; defaults to a spoken estimate


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


@app.post("/sfx")
def sfx(req: SFXRequest):
    """Generate a sound effect for a single selected line (from the editor)."""
    text = req.text.strip()
    if len(text) < 4:
        raise HTTPException(status_code=400, detail="Line too short to score a sound for.")

    seconds = float(req.duration_s) if req.duration_s else _estimate_seconds(text)
    seconds = max(1.0, min(seconds, SFX_MAX_SECONDS))
    prompt = _derive_sfx_prompt(text, req.viral_score)
    wav = _generate_sfx(prompt, seconds)

    return {
        "prompt": prompt,
        "audio_b64": base64.b64encode(wav).decode("ascii"),
        "sample_rate": SFX_SAMPLE_RATE,
        "duration_s": round(seconds, 2),
        "model": SFX_MODEL_NAME,
    }
