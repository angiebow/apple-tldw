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
import os
import re

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

SUMM_MAX_INPUT  = 512   # Pegasus encoder cap (tokens)
SCORE_MAX_LEN   = 64    # detector/reranker were trained at max_length=64
MIN_LINE_WORDS  = 4     # drop trivially short lines

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
    # chunk by tokens, summarize each, then summarize the concatenation
    chunks = [tok.decode(ids[i:i + SUMM_MAX_INPUT], skip_special_tokens=True)
              for i in range(0, len(ids), SUMM_MAX_INPUT)]
    partial = " ".join(_one(c) for c in chunks)
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


# ── API ──────────────────────────────────────────────────────────────────
app = FastAPI(title="tldw highlighter")


class HighlightRequest(BaseModel):
    text: str
    top_k: int = 10


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
