"""
tldw — Phase 1: Topic Segmentation backend.

A small FastAPI service that wraps BERTopic. It takes a transcript (a list of
timestamped utterances), clusters them into topics, then walks the utterances in
time order and merges consecutive same-topic utterances into contiguous
*segments*. Those segments are the candidate boundaries for downstream phases
(extractive ranking, sentiment, title generation, etc.).

Endpoints:
    GET  /health   -> liveness + whether the embedding model is warmed up
    GET  /sample   -> the bundled sample transcript (for quick curl testing)
    POST /segment  -> run BERTopic and return contiguous topic segments

Run:
    ./run.sh            (or)   uvicorn server:app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("tldw")

SAMPLE_PATH = Path(__file__).parent / "sample_transcript.json"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

app = FastAPI(title="tldw — Topic Segmentation", version="0.1.0")

# The SwiftUI client calls from localhost; allow it broadly for the PoC.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Wire models / request + response schemas
# --------------------------------------------------------------------------- #
class Utterance(BaseModel):
    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    text: str


class SegmentRequest(BaseModel):
    utterances: List[Utterance]
    # Smallest number of utterances that may form a topic. Kept low for the PoC
    # because podcast samples are short.
    min_topic_size: int = 2


class Segment(BaseModel):
    topic_id: int
    label: str
    keywords: List[str]
    start: float
    end: float
    utterance_count: int
    text: str
    is_outlier: bool


class SegmentResponse(BaseModel):
    segments: List[Segment]
    topic_count: int
    outlier_utterances: int
    embedding_model: str


# --------------------------------------------------------------------------- #
# Lazy, cached model loading. BERTopic + sentence-transformers are heavy and the
# first import downloads the embedding model, so we build everything on demand
# and reuse the embedding model across requests.
# --------------------------------------------------------------------------- #
_embedding_model = None


def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        log.info("Loading embedding model %s (first run downloads it)...", EMBEDDING_MODEL_NAME)
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        log.info("Embedding model ready.")
    return _embedding_model


def build_topic_model(n_docs: int, min_topic_size: int):
    """Construct a BERTopic configured to behave on short transcripts.

    The stock UMAP/HDBSCAN defaults assume thousands of documents and silently
    collapse everything into a single outlier topic on small inputs. We scale
    the neighborhood/component sizes down to the corpus size.
    """
    from bertopic import BERTopic
    from hdbscan import HDBSCAN
    from umap import UMAP
    from sklearn.feature_extraction.text import CountVectorizer

    n_neighbors = max(2, min(15, n_docs - 1))
    n_components = max(2, min(5, n_docs - 2))

    umap_model = UMAP(
        n_neighbors=n_neighbors,
        n_components=n_components,
        min_dist=0.0,
        metric="cosine",
        random_state=42,  # deterministic output for a reproducible PoC
    )
    hdbscan_model = HDBSCAN(
        min_cluster_size=max(2, min_topic_size),
        metric="euclidean",
        cluster_selection_method="eom",
        prediction_data=True,
    )
    # Drop English stop words so topic keywords are meaningful.
    vectorizer_model = CountVectorizer(stop_words="english")

    return BERTopic(
        embedding_model=get_embedding_model(),
        umap_model=umap_model,
        hdbscan_model=hdbscan_model,
        vectorizer_model=vectorizer_model,
        min_topic_size=max(2, min_topic_size),
        calculate_probabilities=False,
        verbose=False,
    )


def make_label(topic_id: int, model) -> tuple[str, List[str]]:
    """Return a human label and keyword list for a topic id."""
    if topic_id == -1:
        return ("Transition / off-topic", [])
    words = [w for w, _ in (model.get_topic(topic_id) or []) if w][:8]
    label = " · ".join(words[:3]) if words else f"Topic {topic_id}"
    return (label, words)


def segment_transcript(req: SegmentRequest) -> SegmentResponse:
    docs = [u.text for u in req.utterances]
    n = len(docs)
    if n < 4:
        raise HTTPException(
            status_code=422,
            detail="Need at least 4 utterances to run topic segmentation.",
        )

    model = build_topic_model(n, req.min_topic_size)
    topics, _ = model.fit_transform(docs)

    # Walk utterances in time order and merge consecutive same-topic runs.
    segments: List[Segment] = []
    outlier_count = 0
    distinct_topics = set()

    for utt, topic_id in zip(req.utterances, topics):
        topic_id = int(topic_id)
        if topic_id == -1:
            outlier_count += 1
        else:
            distinct_topics.add(topic_id)

        if segments and segments[-1].topic_id == topic_id:
            seg = segments[-1]
            seg.end = utt.end
            seg.utterance_count += 1
            seg.text = f"{seg.text} {utt.text}".strip()
        else:
            label, keywords = make_label(topic_id, model)
            segments.append(
                Segment(
                    topic_id=topic_id,
                    label=label,
                    keywords=keywords,
                    start=utt.start,
                    end=utt.end,
                    utterance_count=1,
                    text=utt.text,
                    is_outlier=(topic_id == -1),
                )
            )

    log.info("Segmented %d utterances into %d segments (%d topics, %d outliers).",
             n, len(segments), len(distinct_topics), outlier_count)

    return SegmentResponse(
        segments=segments,
        topic_count=len(distinct_topics),
        outlier_utterances=outlier_count,
        embedding_model=EMBEDDING_MODEL_NAME,
    )


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok", "model_warm": _embedding_model is not None,
            "embedding_model": EMBEDDING_MODEL_NAME}


@app.get("/sample")
def sample():
    if not SAMPLE_PATH.exists():
        raise HTTPException(status_code=404, detail="sample_transcript.json not found")
    return json.loads(SAMPLE_PATH.read_text())


@app.post("/segment", response_model=SegmentResponse)
def segment(req: SegmentRequest):
    return segment_transcript(req)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
