# tldw — too long; didn't watch

> Takes long-form video content such as podcasts and newscaster footage,
> extracts the audio, transcribes it via STT, and runs an NLP pipeline to
> automatically identify the most engaging segments — outputting short clips
> with generated titles and summaries.

The repo is a set of research PoCs that build up the NLP core of that product,
plus a small SwiftUI macOS app for the summarization step.

---

## End-to-end workflow

```
┌───────────────┐   ┌─────┐   ┌──────────────────────────────┐   ┌──────────────────┐   ┌────────────────────────┐
│    Audio      │──▶│ STT │──▶│  Transcript Content          │──▶│  Timestamps &    │──▶│  Decorated generation  │
│  Extraction   │   │     │   │  Highlighter  (NLP core)     │   │  Video Cutting   │   │  captions · emojis ·   │
└───────────────┘   └─────┘   └──────────────────────────────┘   └──────────────────┘   │  bloopers              │
                                                                                          └────────────────────────┘
```

Audio is pulled from the source video and transcribed. The **Transcript Content
Highlighter** — the part these PoCs focus on — decides *which lines are worth
clipping*. The chosen spans are mapped back to timestamps, the video is cut, and
the clips are decorated (captions, emojis, blooper reels) for publishing as
shorts.

---

## Transcript Content Highlighter pipeline

The highlighter is a five-step pipeline. Each step has a PoC notebook in this
repo.

| # | Process | Model / tool | PoC folder |
|---|------------------------------------------------|------------------------------------------|--------------------------|
| 1 | **Transcript Summarizer**                      | `google/pegasus-cnn_dailymail`           | `poc-summarizer/`        |
| 2 | **Lines & Summary Embedding**                  | `sentence-transformers/all-mpnet-base-v2`| `poc-lines-relevancy/`   |
| 3 | **Lines & Summary Similarity**                 | cosine similarity                        | `poc-lines-relevancy/`   |
| 4 | **Virality Detector** — single-label classification | `distilbert` (fine-tuned)           | `poc-content-highlighter/` |
| 5 | **Virality Reranker** — regression             | `bert-base` (fine-tuned)                 | `poc-content-highlighter/` |

**How the steps chain:**

1. **Summarize** the whole transcript with Pegasus → a short "what this video is
   about" gist.
2. **Embed** every transcript line *and* the summary with the **same** encoder
   (`all-mpnet-base-v2`, `normalize_embeddings=True` so dot-product = cosine).
3. **Rank lines by cosine similarity to the summary** → which lines actually
   carry the gist (relevance).
4. **Virality Detector** scores each candidate line: *is this clip-worthy at
   all?* (binary gate).
5. **Virality Reranker** scores the survivors on a continuous scale: *how
   clip-worthy?* — used to order them and pick the top highlights.

So a good highlight is both **on-topic** (steps 2–3) and **engaging** (steps
4–5).

---

## The virality models & their dataset

Steps 4–5 are trained on the **YouTube trending titles** dataset
(`poc-content-highlighter/youtube_trending_videos_global*.parquet`).
`preprocess_youtube_trending.ipynb` turns the raw dump into labeled splits under
`processed/`.

Both models read the **same `video_title` text** but a **different label
column**, derived from the same signal — a title's `engagement_rate`
((likes + comments) / views) ranked **within its category**:

| video_title                          | viral_score | viral_label |
|--------------------------------------|:-----------:|:-----------:|
| "I Tried This for 30 Days"           | 0.92        | **1**       |
| "You Won't Believe What Happened"    | 0.78        | **1**       |
| "My Morning Routine"                 | 0.55        | **NaN**     |
| "Weekly Update #42"                  | 0.40        | **NaN**     |
| "Quarterly Earnings Call"            | 0.12        | **0**       |

- **`viral_score`** — the continuous within-category percentile (0–1). →
  **Reranker (regression)**, trained on **all** rows. Learns the full ordering.
- **`viral_label`** — top quartile = `1`, bottom quartile = `0`, **middle 50% =
  `NaN`**. → **Detector (single-label classification)**, which **drops the NaN
  rows** so it trains on a clean top-vs-bottom decision boundary.

In short: the reranker learns the *number*; the detector learns the *bucket*
(with the ambiguous middle thrown away).

### Why two models (detector + reranker)?

A cheap **filter → rank** cascade. The lightweight detector is a high-recall
gate over every line ("could this be a highlight?"); the heavier reranker only
scores the survivors and orders them for top-N selection. Classification and
ranking are different objectives — a classifier's probability is a poor sort
key, so the regression head exists to give a smooth, fine-grained ordering.

### Model selection findings

Both stages were benchmarked against alternatives (multilingual, since titles
are global). **Chosen models in bold.**

**Detector** — picked on threshold-time metrics (F1 / accuracy), since it runs
as a yes/no gate:

| model                                     | params (M) | AUC    | F1     | accuracy | train (s) |
|-------------------------------------------|:----------:|:------:|:------:|:--------:|:---------:|
| **distilbert-base-multilingual-cased**    | 135        | 0.8956 | **0.8326** | **0.8295** | 136   |
| microsoft/Multilingual-MiniLM-L12-H384    | 118        | 0.8983 | 0.8165 | 0.8157   | 133       |
| bert-base-multilingual-cased              | 178        | 0.8880 | 0.7926 | 0.7926   | 191       |

**Reranker** — picked on **NDCG@10** (top-of-list ranking quality is what a
reranker is for); the chosen model is also smaller and faster:

| model                            | params (M) | Spearman | NDCG@10 | MSE    | train (s) |
|----------------------------------|:----------:|:--------:|:-------:|:------:|:---------:|
| **bert-base-multilingual-cased** | 178        | 0.5699   | **0.8494** | 0.0572 | 755    |
| xlm-roberta-base                 | 278        | 0.5864   | 0.7146  | 0.0547 | 1073      |

> Spearman ≈ 0.57 means **title text alone is only a modest virality
> predictor**. The engineered title features the preprocessing already computes
> (`has_number`, `has_question`, `upper_ratio`, `title_word_count`,
> `trending_country_count`, …) are a likely next lever beyond swapping encoders.

---

## Summarizer comparison (step 1)

`poc-summarizer/no1_compare_summarizers.ipynb` scores three seq2seq summarizers
on CNN/DailyMail (`test.csv`) against the human `highlights`, using ROUGE,
BERTScore and METEOR:

| model                          | notes                                            |
|--------------------------------|--------------------------------------------------|
| `facebook/bart-large-cnn`      | BART, fine-tuned on CNN/DailyMail                |
| `google/pegasus-cnn_dailymail` | Pegasus, fine-tuned on CNN/DailyMail (**chosen** for the pipeline) |
| `allenai/led-large-16384-arxiv`| LED long-doc encoder-decoder, fine-tuned on arXiv |

`evaluate_summarizers.py` holds the metric helpers (ROUGE = n-gram/LCS overlap,
BERTScore = contextual-embedding similarity, METEOR = stemmed + synonym-aware
alignment).

---

## Repo layout

```
poc-summarizer/                 Step 1 — transcript summarization
  no1_compare_summarizers.ipynb   BART vs Pegasus vs LED on CNN/DailyMail
  evaluate_summarizers.py         ROUGE / BERTScore / METEOR helpers
  {train,validation,test}.csv     CNN/DailyMail splits (git-ignored, large)

poc-lines-relevancy/            Steps 2–3 — ground the summary back to lines
  no2_poc_line_similarity.ipynb   embed lines + summary, cosine-rank by relevance

poc-content-highlighter/        Steps 4–5 — virality detector + reranker
  preprocess_youtube_trending.ipynb   raw trending parquet → labeled splits
  no3_compare_detectors.ipynb         detector model comparison (classification)
  no4_compare_rerankers.ipynb         reranker model comparison (regression)
  youtube_trending_videos_global*.parquet  raw data (git-ignored, large)
  processed/{train,val,test}.parquet       derived splits (git-ignored)
  models/                                   fine-tuned checkpoints (git-ignored)

tldw/                           SwiftUI macOS app (summarization front-end)
tldw.xcodeproj/
```

> The large datasets and model checkpoints are intentionally **git-ignored** —
> regenerate them by running the preprocessing/training notebooks.

---

## Running the PoCs

Each PoC is a self-contained Jupyter notebook that `pip install`s its own deps in
the first cell; on Apple Silicon they run on the **MPS** GPU backend when
available. Typical order:

1. `poc-summarizer/no1_compare_summarizers.ipynb` — pick a summarizer.
2. `poc-lines-relevancy/no2_poc_line_similarity.ipynb` — relevance ranking.
3. `poc-content-highlighter/preprocess_youtube_trending.ipynb` — build the
   labeled splits, then `no3_compare_detectors.ipynb` and
   `no4_compare_rerankers.ipynb` to train/compare the virality models.

---

## macOS app (summarization front-end)

The SwiftUI app in `tldw/` is a thin client for the summarization step. It talks
to a local summarization service over `http://127.0.0.1:8000`
(`/health`, `/summarize`) and shows the generated summary; an optional
**Reference summary** field renders an evaluation card (ROUGE / BERTScore /
METEOR) for the generated summary against your reference.

Open `tldw.xcodeproj` in Xcode and press **⌘R**. The window opens with a sample
passage; when the status pill is **green** ("backend up"), tap **Summarize**.

`POST /summarize` data contract:

```jsonc
// request
{ "text": "…long text…", "max_length": 130, "min_length": 30, "reference": "…optional gold summary…" }

// response
{ "summary": "…", "model": "…", "chunk_count": 1,
  "input_chars": 1820, "summary_chars": 320,
  "metrics": { "rouge": {…}, "bertscore": {…}, "meteor": 0.74 } }  // metrics null unless `reference` sent
```

> Long transcripts are chunked at sentence boundaries to respect the model's
> input-token limit, then hierarchically re-summarized into one overall summary.
