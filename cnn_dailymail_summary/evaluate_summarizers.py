"""
Compare summarization models on CNN/DailyMail test.csv.

Runs each model over every `article` in test.csv, scores the generated summary
against the `highlights` reference with ROUGE / BERTScore / METEOR, and prints a
side-by-side comparison of the averaged scores.

Defaults compare facebook/bart-large-cnn and google/pegasus-cnn_dailymail.
ROUGE/METEOR helpers are reused from ../backend/evaluation.py.

Pegasus needs extra tokenizer deps: pip install sentencepiece protobuf

Usage (use the backend venv, which has all deps):
    ../backend/venv/bin/python evaluate_summarizers.py                 # both models, all rows
    ../backend/venv/bin/python evaluate_summarizers.py --limit 1000    # quick subset
    ../backend/venv/bin/python evaluate_summarizers.py --models facebook/bart-large-cnn
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import pandas as pd
import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

# Locate this file's dir, falling back to cwd if pasted into a Jupyter cell.
try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    BASE_DIR = os.getcwd()

# Reuse the same metric helpers the backend /evaluate endpoint uses.
sys.path.append(os.path.join(BASE_DIR, "..", "backend"))
import evaluation  # noqa: E402  (rouge_scores, meteor)

DEFAULT_MODELS = ["facebook/bart-large-cnn", "google/pegasus-cnn_dailymail"]


def generate_summaries(model_name: str, articles: list[str], device: str, args) -> list[str]:
    """Load `model_name` and summarize every article (batched)."""
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    tokenizer.model_max_length = 1024  # BART and Pegasus both cap the encoder at 1024
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(device).eval()

    preds: list[str] = []
    started = time.time()
    for start in range(0, len(articles), args.batch_size):
        batch = articles[start : start + args.batch_size]
        inputs = tokenizer(
            batch, max_length=1024, truncation=True, padding=True, return_tensors="pt"
        ).to(device)
        with torch.no_grad():
            ids = model.generate(
                **inputs,
                max_length=args.max_summary_len,
                min_length=args.min_summary_len,
                num_beams=4,
                do_sample=False,
            )
        for txt in tokenizer.batch_decode(ids, skip_special_tokens=True):
            # Pegasus emits "<n>" as a sentence separator; normalise for fair scoring.
            preds.append(txt.replace("<n>", " ").strip())
        done = min(start + args.batch_size, len(articles))
        rate = done / (time.time() - started)
        print(f"  [{model_name}] generated {done}/{len(articles)}  ({rate:.1f} docs/s)", end="\r")
    print()
    return preds


def score(preds: list[str], references: list[str]) -> dict[str, float]:
    """Average ROUGE / BERTScore-F1 / METEOR over all (pred, reference) pairs."""
    rouge = [evaluation.rouge_scores(p, r) for p, r in zip(preds, references)]
    meteor = [evaluation.meteor(p, r) for p, r in zip(preds, references)]

    from bert_score import score as bert_score

    _, _, f1 = bert_score(preds, references, lang="en", rescale_with_baseline=True)
    n = len(preds)
    return {
        "rouge1": sum(x["rouge1"] for x in rouge) / n,
        "rouge2": sum(x["rouge2"] for x in rouge) / n,
        "rougeL": sum(x["rougeL"] for x in rouge) / n,
        "bertscore_f1": f1.mean().item(),
        "meteor": sum(meteor) / n,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--test-csv", default=os.path.join(BASE_DIR, "test.csv"))
    ap.add_argument("--limit", type=int, default=None, help="evaluate only the first N rows (default: all)")
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--max-summary-len", type=int, default=128)
    ap.add_argument("--min-summary-len", type=int, default=30)
    args, _ = ap.parse_known_args()

    device = (
        "cuda" if torch.cuda.is_available()
        else "mps" if torch.backends.mps.is_available()
        else "cpu"
    )
    print(f"Device: {device}")

    df = pd.read_csv(args.test_csv).dropna(subset=["article", "highlights"])
    df = df[(df["article"].str.strip() != "") & (df["highlights"].str.strip() != "")]
    if args.limit is not None:
        df = df.head(args.limit)
    df = df.reset_index(drop=True)
    articles, references = df["article"].tolist(), df["highlights"].tolist()
    print(f"Evaluating {len(df)} examples across {len(args.models)} model(s)\n")

    results: dict[str, dict[str, float]] = {}
    for model_name in args.models:
        print(f"=== {model_name} ===")
        preds = generate_summaries(model_name, articles, device, args)
        results[model_name] = score(preds, references)
        print()

    # --- comparison table ---
    metrics = ["rouge1", "rouge2", "rougeL", "bertscore_f1", "meteor"]
    width = max(len(m) for m in results)
    header = f"{'model':<{width}}  " + "  ".join(f"{m:>12}" for m in metrics)
    print(f"\n=== Comparison over {len(df)} examples ===")
    print(header)
    print("-" * len(header))
    for model_name, sc in results.items():
        print(f"{model_name:<{width}}  " + "  ".join(f"{sc[k]:>12.4f}" for k in metrics))


if __name__ == "__main__":
    main()
