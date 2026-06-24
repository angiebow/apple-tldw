"""
tldw — Overall Summary backend (Phase 1 of the summary pipeline).

A small FastAPI service that wraps a BART summarization model
(`facebook/bart-large-cnn`). It accepts raw text and returns an abstractive
summary. BART's encoder caps at ~1024 tokens, so long inputs are split into
token-aware chunks; each chunk is summarized and, if there were several, the
chunk summaries are summarized once more into a single overall summary.

Endpoints:
    GET  /health     -> liveness + whether the model is warmed up
    POST /summarize  -> { text } -> { summary }
    POST /evaluate   -> { prediction, reference } -> ROUGE / BERTScore / METEOR

Run:
    ./run.sh    (or)   uvicorn server:app --reload --port 8000
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import evaluation

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("tldw")

SUMMARIZER_MODEL = "facebook/bart-large-cnn"
# BART encoder limit is 1024 tokens; stay safely under it per chunk.
MAX_INPUT_TOKENS = 900

app = FastAPI(title="tldw — Overall Summary (BART)", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Wire schemas
# --------------------------------------------------------------------------- #
class SummarizeRequest(BaseModel):
    text: str
    max_length: int = Field(130, description="Max tokens in the generated summary")
    min_length: int = Field(30, description="Min tokens in the generated summary")
    reference: Optional[str] = Field(
        None,
        description="Optional gold summary; if given, the response includes "
        "ROUGE / BERTScore / METEOR scores for the generated summary.",
    )


class SummarizeResponse(BaseModel):
    summary: str
    model: str
    chunk_count: int
    input_chars: int
    summary_chars: int
    metrics: Optional[dict] = Field(
        None, description="Evaluation scores vs. `reference`, when one was provided."
    )


class EvaluateRequest(BaseModel):
    prediction: str = Field(..., description="The generated summary to score")
    reference: str = Field(..., description="The gold / human reference summary")


class EvaluateResponse(BaseModel):
    rouge: dict
    bertscore: dict
    meteor: float


# --------------------------------------------------------------------------- #
# Lazy, cached model. transformers + torch are heavy and the first call
# downloads the BART weights (~1.6GB), so build on demand and reuse.
# --------------------------------------------------------------------------- #
_summarizer = None


def get_summarizer():
    global _summarizer
    if _summarizer is None:
        import torch
        from transformers import pipeline

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        log.info("Loading %s on %s (first run downloads ~1.6GB)...", SUMMARIZER_MODEL, device)
        _summarizer = pipeline("summarization", model=SUMMARIZER_MODEL, device=device)
        log.info("Summarizer ready.")
    return _summarizer


def chunk_by_tokens(text: str, tokenizer, max_tokens: int) -> List[str]:
    """Split text into chunks whose tokenized length stays under max_tokens.

    Greedily packs whole sentences so we never cut mid-sentence. Falls back to
    the whole text if it already fits.
    """
    import re

    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: List[str] = []
    current: List[str] = []

    for sentence in sentences:
        candidate = " ".join(current + [sentence])
        token_count = len(tokenizer.encode(candidate, add_special_tokens=False))
        if token_count > max_tokens and current:
            chunks.append(" ".join(current))
            current = [sentence]
        else:
            current.append(sentence)

    if current:
        chunks.append(" ".join(current))
    return chunks or [text]


def summarize_text(req: SummarizeRequest) -> SummarizeResponse:
    text = req.text.strip()
    if len(text) < 40:
        raise HTTPException(status_code=422, detail="Provide at least ~40 characters of text to summarize.")

    summarizer = get_summarizer()
    chunks = chunk_by_tokens(text, summarizer.tokenizer, MAX_INPUT_TOKENS)

    def run(passage: str) -> str:
        # Don't ask for a summary longer than the input itself.
        input_tokens = len(summarizer.tokenizer.encode(passage, add_special_tokens=False))
        max_len = max(req.min_length + 5, min(req.max_length, input_tokens))
        out = summarizer(
            passage,
            max_length=max_len,
            min_length=min(req.min_length, max_len - 1),
            do_sample=False,
            truncation=True,
        )
        return out[0]["summary_text"].strip()

    partials = [run(chunk) for chunk in chunks]

    if len(partials) == 1:
        summary = partials[0]
    else:
        # Second pass: condense the per-chunk summaries into one overall summary.
        summary = run(" ".join(partials))

    log.info("Summarized %d chars across %d chunk(s) -> %d chars.",
             len(text), len(chunks), len(summary))

    metrics = None
    if req.reference and req.reference.strip():
        metrics = evaluation.evaluate(summary, req.reference.strip())

    return SummarizeResponse(
        summary=summary,
        model=SUMMARIZER_MODEL,
        chunk_count=len(chunks),
        input_chars=len(text),
        summary_chars=len(summary),
        metrics=metrics,
    )


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok", "model_warm": _summarizer is not None, "model": SUMMARIZER_MODEL}


@app.post("/summarize", response_model=SummarizeResponse)
def summarize(req: SummarizeRequest):
    return summarize_text(req)


@app.post("/evaluate", response_model=EvaluateResponse)
def evaluate(req: EvaluateRequest):
    prediction = req.prediction.strip()
    reference = req.reference.strip()
    if not prediction or not reference:
        raise HTTPException(
            status_code=422, detail="Both `prediction` and `reference` must be non-empty."
        )
    return EvaluateResponse(**evaluation.evaluate(prediction, reference))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
