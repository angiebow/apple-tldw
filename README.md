# tldw — too long; didn't watch

Extract the most engaging highlights from long-form video (podcasts, talks) and
turn them into shorts, using speech recognition + NLP.

## Pipeline

| # | Phase | Technique |
|---|----------------------------|-----------------------------------------------|
| 1 | **Overall Summary**        | **BART** (`facebook/bart-large-cnn`) ⟵ *implemented (PoC)* |
| 2 | Similarity                 | per-line embeddings → cosine (sentence) / BM25 (lexical) |
| 3 | Clickbait / viral detection| distilbert / roberta fine-tuned on trending YouTube titles |

---

## Phase 1 — Overall Summary (this PoC)

The app takes raw text and returns an abstractive summary produced by **BART**.
BART's encoder caps at ~1024 tokens, so longer inputs are split into token-aware
chunks; each chunk is summarized and, when there are several, the chunk
summaries are summarized once more into a single overall summary.

### Architecture

```
┌──────────────────────┐   POST /summarize (JSON)  ┌───────────────────────────┐
│  tldw  (SwiftUI app)  │ ───────────────────────▶ │  FastAPI sidecar (Python) │
│  macOS, sandboxed     │ ◀─────────────────────── │  BART summarization       │
└──────────────────────┘        summary            └───────────────────────────┘
        127.0.0.1:8000                               facebook/bart-large-cnn
```

- **Why a sidecar?** BART runs via Hugging Face `transformers` (Python). The app
  talks to it over `localhost`; the same server will host the Similarity and
  clickbait phases next.
- The macOS app is sandboxed; outgoing network is enabled via the
  `ENABLE_OUTGOING_NETWORK_CONNECTIONS` build setting.
- On Apple Silicon the model runs on the **MPS** GPU backend when available.

### Repo layout

```
backend/
  server.py          FastAPI app: /health, /summarize, /evaluate
  evaluation.py      summary quality metrics: ROUGE, BERTScore, METEOR
  requirements.txt   transformers, torch, fastapi, uvicorn (+ sentence-transformers for phase 2)
  setup.sh / run.sh  create venv / start server
tldw/                SwiftUI app (filesystem-synchronized Xcode group)
  Models.swift               Codable wire types
  SummarizationService.swift HTTP client
  SummarizationViewModel.swift @Observable state
  SampleText.swift           bundled sample passage
  ContentView.swift          input pane + summary pane
tldw.xcodeproj/
```

---

## Run it

### 1. Backend

```bash
cd backend
./setup.sh          # one-time: creates ./venv and installs the stack
./run.sh            # serves http://127.0.0.1:8000
```

> The **first** `/summarize` call downloads `facebook/bart-large-cnn` (~1.6GB)
> and is slow; subsequent calls are fast.

Sanity-check without the app:

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/summarize \
  -H 'Content-Type: application/json' \
  -d '{"text": "<paste a few paragraphs here>"}' | python3 -m json.tool
```

### 2. App

Open `tldw.xcodeproj` in Xcode and press **⌘R** (only Command Line Tools are
installed on the CLI, so the app builds from Xcode). The window opens with a
sample passage; when the status pill is **green** ("backend up"), tap
**Summarize**.

The input pane has a second, optional **Reference summary** field. Fill it in
and the summary pane shows an **evaluation card** with ROUGE, BERTScore and
METEOR scores for the generated summary against your reference.

---

## Data contract

`POST /summarize`

```jsonc
// request
{ "text": "…long text…", "max_length": 130, "min_length": 30 }

// response
{ "summary": "…", "model": "facebook/bart-large-cnn",
  "chunk_count": 1, "input_chars": 1820, "summary_chars": 320,
  "metrics": null }
```

Pass an optional `reference` (gold summary) to `/summarize` and the response's
`metrics` field is populated with the scores below.

`POST /evaluate` — score a summary against a reference on its own:

```jsonc
// request
{ "prediction": "…generated summary…", "reference": "…gold summary…" }

// response
{ "rouge":     { "rouge1": 0.57, "rouge2": 0.32, "rougeL": 0.57 },
  "bertscore": { "precision": 0.87, "recall": 0.85, "f1": 0.86 },
  "meteor":    0.74 }
```

- **ROUGE** — n-gram / longest-common-subsequence overlap (surface, recall-oriented).
- **BERTScore** — contextual-embedding cosine similarity (semantic; credits paraphrase).
- **METEOR** — unigram alignment with stemming + WordNet synonymy.

> The first `/evaluate` call downloads the BERTScore RoBERTa model and NLTK
> WordNet data, so it is slow once; subsequent calls are fast.

## PoC notes

- `max_length` / `min_length` are summary length bounds in tokens; tune per use.
- Long transcripts are chunked at sentence boundaries to respect BART's 1024-token
  limit, then hierarchically re-summarized.
- Next phases reuse this sidecar: Similarity (cosine/BM25 over per-line embeddings)
  and clickbait/viral scoring (fine-tuned distilbert/roberta).
