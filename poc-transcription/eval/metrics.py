"""
ASR Evaluation Metrics
=======================
WER/CER scoring for transcript output against ground truth, with
per-segment breakdown to find where the model is actually failing
(vs. aggregate WER hiding it).

Requires: pip install jiwer --break-system-packages
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import jiwer

# Standard normalization: lowercase, strip punctuation, collapse whitespace.
# This matches what most published Whisper WER benchmarks use — don't skip
# it or your numbers will be inflated by punctuation/casing mismatches that
# have nothing to do with actual transcription accuracy.
NORMALIZER = jiwer.Compose([
    jiwer.ToLowerCase(),
    jiwer.RemovePunctuation(),
    jiwer.RemoveMultipleSpaces(),
    jiwer.Strip(),
    jiwer.RemoveEmptyStrings(),
])


@dataclass
class EvalResult:
    wer: float
    cer: float
    substitutions: int
    deletions: int
    insertions: int
    hits: int
    ref_word_count: int


def compute_metrics(reference: str, hypothesis: str) -> EvalResult:
    """Compute WER/CER for a single reference/hypothesis pair."""
    ref_norm = NORMALIZER(reference)
    hyp_norm = NORMALIZER(hypothesis)

    wer_out = jiwer.process_words(ref_norm, hyp_norm)
    cer_out = jiwer.process_characters(ref_norm, hyp_norm)

    return EvalResult(
        wer=wer_out.wer,
        cer=cer_out.cer,
        substitutions=wer_out.substitutions,
        deletions=wer_out.deletions,
        insertions=wer_out.insertions,
        hits=wer_out.hits,
        ref_word_count=len(ref_norm.split()),
    )


def load_transcript_text(segments_json_path: str) -> str:
    """Concatenate a transcript_segments.json output into a single string."""
    with open(segments_json_path, "r", encoding="utf-8") as f:
        segments = json.load(f)
    return " ".join(s["text"] for s in segments)


def evaluate_against_reference(
    segments_json_path: str,
    reference_text: str,
) -> EvalResult:
    """
    Score a transcriber output file against a ground-truth reference string.
    """
    hypothesis = load_transcript_text(segments_json_path)
    return compute_metrics(reference_text, hypothesis)


def evaluate_segments_individually(
    hyp_segments_json_path: str,
    ref_segments_json_path: str,
) -> List[EvalResult]:
    """
    Per-segment WER, assuming hyp and ref segment lists are aligned 1:1
    (e.g. same VAD boundaries used to produce both). Useful for finding
    *which* chunks are dragging your aggregate WER down instead of just
    staring at one global number.
    """
    with open(hyp_segments_json_path, "r", encoding="utf-8") as f:
        hyp_segments = json.load(f)
    with open(ref_segments_json_path, "r", encoding="utf-8") as f:
        ref_segments = json.load(f)

    if len(hyp_segments) != len(ref_segments):
        raise ValueError(
            f"Segment count mismatch: {len(hyp_segments)} hyp vs "
            f"{len(ref_segments)} ref. Per-segment eval needs aligned "
            f"segments — use evaluate_against_reference() for unaligned text."
        )

    return [
        compute_metrics(ref["text"], hyp["text"])
        for ref, hyp in zip(ref_segments, hyp_segments)
    ]


def print_report(result: EvalResult, label: str = "Overall") -> None:
    print(f"\n📊 {label}")
    print(f"   WER: {result.wer:.2%}  ({result.hits} hits / {result.ref_word_count} ref words)")
    print(f"   CER: {result.cer:.2%}")
    print(f"   Sub: {result.substitutions}  Del: {result.deletions}  Ins: {result.insertions}")