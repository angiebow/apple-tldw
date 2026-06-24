"""Fine-tune + save the virality reranker (bert-base, regression).

Adapted from no4_compare_rerankers.ipynb. Unlike the comparison notebook
(`save_strategy="no"`), this trains the chosen model and *saves* it so the
backend can load it.

    python train_reranker.py

Output: models/bert-ranker/  (model.safetensors + tokenizer files)
Target: predict `viral_score` (0-1 within-category engagement percentile) from
`video_title`.
"""
import os
import time

# Lift PyTorch's MPS memory ceiling; must run before torch is imported.
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")

import numpy as np
import pandas as pd
import torch
from datasets import Dataset
from scipy.stats import spearmanr
from sklearn.metrics import ndcg_score
from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                          DataCollatorWithPadding, Trainer, TrainingArguments)

HERE       = os.path.dirname(os.path.abspath(__file__))
PROC_DIR   = os.path.join(HERE, "processed")
OUT_DIR    = os.path.join(HERE, "models", "bert-ranker")
MODEL_NAME = "bert-base-multilingual-cased"

MAX_LEN    = 64
EPOCHS     = 3
BATCH_SIZE = 8       # MPS-friendly; drop to 4 if you hit OOM
GRAD_ACCUM = 2       # effective batch = 16
LR         = 2e-5
NDCG_K     = 10

DEVICE = ("cuda" if torch.cuda.is_available()
          else "mps" if torch.backends.mps.is_available() else "cpu")


def load_split(name):
    df = pd.read_parquet(os.path.join(PROC_DIR, f"{name}.parquet")).copy()
    df["viral_score"] = df["viral_score"].astype("float32")
    return df


def make_ds(df, tok):
    d = Dataset.from_pandas(
        df[["video_title", "viral_score"]]
        .rename(columns={"video_title": "text", "viral_score": "labels"}),
        preserve_index=False,
    )
    return d.map(lambda b: tok(b["text"], truncation=True, max_length=MAX_LEN),
                 batched=True, remove_columns=["text"])


def reg_metrics(eval_pred):
    preds, labels = eval_pred
    preds = preds.squeeze()
    return {"spearman": spearmanr(preds, labels).correlation,
            "mse": float(np.mean((preds - labels) ** 2))}


def main():
    print("device:", DEVICE)
    train_df, val_df, test_df = load_split("train"), load_split("val"), load_split("test")
    print(f"rows -> train {len(train_df)} | val {len(val_df)} | test {len(test_df)}")

    tok = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME, num_labels=1)
    model.config.problem_type = "regression"

    args = TrainingArguments(
        output_dir=os.path.join(HERE, "models", "_bert-ranker-trainer"),
        num_train_epochs=EPOCHS, per_device_train_batch_size=BATCH_SIZE,
        per_device_eval_batch_size=BATCH_SIZE, gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR, weight_decay=0.01,
        eval_strategy="epoch", save_strategy="no", logging_steps=100, report_to="none")

    trainer = Trainer(model=model, args=args,
                      train_dataset=make_ds(train_df, tok), eval_dataset=make_ds(val_df, tok),
                      data_collator=DataCollatorWithPadding(tok), processing_class=tok,
                      compute_metrics=reg_metrics)

    t0 = time.time()
    trainer.train()
    print(f"trained in {time.time() - t0:.0f}s")

    # Held-out ranking metrics (sanity check it matches the comparison notebook).
    pred = trainer.predict(make_ds(test_df, tok)).predictions.squeeze()
    true = test_df["viral_score"].to_numpy()
    print(f"test  spearman={spearmanr(pred, true).correlation:.4f}  "
          f"ndcg@{NDCG_K}={ndcg_score([true], [pred], k=NDCG_K):.4f}  "
          f"mse={float(np.mean((pred - true) ** 2)):.4f}")

    os.makedirs(OUT_DIR, exist_ok=True)
    trainer.save_model(OUT_DIR)
    tok.save_pretrained(OUT_DIR)
    print("saved reranker ->", OUT_DIR)


if __name__ == "__main__":
    main()
