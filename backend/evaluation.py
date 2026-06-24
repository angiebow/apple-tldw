"""
Evaluation metrics for the summarization pipeline.

Scores a generated summary (the *prediction* from BART) against a human
*reference* summary using three complementary metrics:

    ROUGE     — n-gram and longest-common-subsequence overlap. Surface-level,
                recall-oriented. Reports ROUGE-1, ROUGE-2 and ROUGE-L F1.
    BERTScore — cosine similarity of contextual token embeddings. Semantic:
                rewards paraphrases that ROUGE would miss. Reports F1.
    METEOR    — unigram alignment with stemming and synonymy (WordNet),
                penalising fragmentation. A single 0–1 score.

Each metric uses a different definition of "close to the reference", so
together they give a rounded picture of summary quality.

The scorers are heavy to construct (BERTScore loads a RoBERTa model, METEOR
needs WordNet), so each is built lazily and cached for reuse.
"""

from __future__ import annotations

import logging
from typing import Dict

log = logging.getLogger("tldw")


# --------------------------------------------------------------------------- #
# ROUGE — rouge-score, stemmed, F-measure for rouge1 / rouge2 / rougeL.
# --------------------------------------------------------------------------- #
_rouge_scorer = None


def _get_rouge_scorer():
    global _rouge_scorer
    if _rouge_scorer is None:
        from rouge_score import rouge_scorer

        _rouge_scorer = rouge_scorer.RougeScorer(
            ["rouge1", "rouge2", "rougeL"], use_stemmer=True
        )
    return _rouge_scorer


def rouge_scores(prediction: str, reference: str) -> Dict[str, float]:
    scorer = _get_rouge_scorer()
    scores = scorer.score(reference, prediction)
    return {key: round(value.fmeasure, 4) for key, value in scores.items()}


# --------------------------------------------------------------------------- #
# BERTScore — bert-score, English. Returns precision / recall / F1.
# --------------------------------------------------------------------------- #
def bertscore(prediction: str, reference: str) -> Dict[str, float]:
    from bert_score import score as bert_score

    precision, recall, f1 = bert_score(
        [prediction], [reference], lang="en", rescale_with_baseline=True
    )
    return {
        "precision": round(precision.item(), 4),
        "recall": round(recall.item(), 4),
        "f1": round(f1.item(), 4),
    }


# --------------------------------------------------------------------------- #
# METEOR — nltk. Needs WordNet + a tokenizer; download once, lazily.
# --------------------------------------------------------------------------- #
_meteor_ready = False


def _ensure_meteor_resources() -> None:
    global _meteor_ready
    if _meteor_ready:
        return

    import nltk

    # Newer punkt ("punkt_tab") and the multilingual WordNet ("omw-1.4") are
    # required by recent nltk; download quietly if missing.
    for resource, path in (
        ("wordnet", "corpora/wordnet"),
        ("omw-1.4", "corpora/omw-1.4"),
        ("punkt", "tokenizers/punkt"),
        ("punkt_tab", "tokenizers/punkt_tab"),
    ):
        try:
            nltk.data.find(path)
        except LookupError:
            log.info("Downloading nltk resource: %s", resource)
            nltk.download(resource, quiet=True)

    _meteor_ready = True


def meteor(prediction: str, reference: str) -> float:
    _ensure_meteor_resources()

    from nltk import word_tokenize
    from nltk.translate.meteor_score import meteor_score

    score = meteor_score([word_tokenize(reference)], word_tokenize(prediction))
    return round(score, 4)


# --------------------------------------------------------------------------- #
# Public entry point: run all three.
# --------------------------------------------------------------------------- #
def evaluate(prediction: str, reference: str) -> Dict[str, object]:
    """Score `prediction` against `reference` with ROUGE, BERTScore and METEOR."""
    return {
        "rouge": rouge_scores(prediction, reference),
        "bertscore": bertscore(prediction, reference),
        "meteor": meteor(prediction, reference),
    }
