# tldw — too long; didn't watch

Extract the most engaging highlights from long-form video (podcasts, talks) and
turn them into shorts, using speech recognition + NLP.

## Pipeline (5 phases)

| # | Phase | Technique |
|---|-------------------------|-----------------|
| 1 | **Topic Segmentation**  | **BERTopic** ⟵ *implemented (PoC)* |
| 2 | Extractive Ranking      | TextRank / LexRank |
| 3 | Sentiment Overlay       | VADER |
| 4 | Short Title Generation  | BART |
| 5 | Voice Activity Detection| Silero-VAD |

---

## Phase 1 — Topic Segmentation (this PoC)

A long transcript is split into timestamped utterances. **BERTopic** embeds and
clusters them into topics; we then walk the utterances in time order and merge
consecutive same-topic runs into **contiguous segments**. Those segments are the
candidate boundaries every later phase builds on (rank them, score sentiment,
title them, trim with VAD).

### Architecture

```
┌──────────────────────┐   POST /segment (JSON)   ┌───────────────────────────┐
│  tldw  (SwiftUI app)  │ ───────────────────────▶ │  FastAPI sidecar (Python) │
│  macOS, sandboxed     │ ◀─────────────────────── │  BERTopic + MiniLM        │
└──────────────────────┘     topic segments        └───────────────────────────┘
        127.0.0.1:8000
```

- **Why a sidecar?** BERTopic (sentence-transformers + UMAP + HDBSCAN) is
  Python-only. The app talks to it over `localhost`. The same server is reused
  by later phases.
- The macOS app is sandboxed; outgoing network is enabled via the
  `ENABLE_OUTGOING_NETWORK_CONNECTIONS` build setting (no entitlements file).

### Repo layout

```
backend/
  server.py              FastAPI app: /health, /sample, /segment
  sample_transcript.json canonical sample (4 topics: AI, fundraising, sleep, space)
  requirements.txt       BERTopic stack
  setup.sh / run.sh      create venv / start server
tldw/                    SwiftUI app (filesystem-synchronized Xcode group)
  Models.swift           Codable wire types
  SegmentationService.swift   HTTP client
  SegmentationViewModel.swift  @Observable state + sample loader
  ContentView.swift      transcript pane + segment cards
  sample_transcript.json bundled copy (shown in the UI before segmenting)
tldw.xcodeproj/
```

---

## Run it

### 1. Start the backend (first time)

```bash
cd backend
./setup.sh          # creates ./venv and installs the BERTopic stack (large)
./run.sh            # serves http://127.0.0.1:8000
```

> The **first** `/segment` call downloads the `all-MiniLM-L6-v2` embedding model
> (~80 MB) and takes ~30–60 s. Subsequent calls are fast.

Sanity-check without the app:

```bash
curl -s localhost:8000/health
curl -s localhost:8000/sample | python3 -m json.tool | head
curl -s -X POST localhost:8000/segment \
  -H 'Content-Type: application/json' \
  --data @<(python3 -c 'import json;d=json.load(open("sample_transcript.json"));print(json.dumps({"utterances":d["utterances"]}))') \
  | python3 -m json.tool
```

### 2. Run the app

Open `tldw.xcodeproj` in Xcode and press **⌘R** (the CLI here only has Command
Line Tools, so the app must be built from Xcode). The window loads the bundled
sample; the status pill turns **green** when the backend is reachable. Tap
**Segment** to cluster the transcript and see the colored topic segments.

---

## Data contract

`POST /segment`

```jsonc
// request
{ "utterances": [ { "start": 0.0, "end": 7.5, "text": "…" } ],
  "min_topic_size": 2 }

// response
{ "segments": [
    { "topic_id": 0, "label": "models · attention · neural",
      "keywords": ["models","attention","neural", "…"],
      "start": 0.0, "end": 54.0, "utterance_count": 7,
      "text": "…", "is_outlier": false } ],
  "topic_count": 4, "outlier_utterances": 1,
  "embedding_model": "all-MiniLM-L6-v2" }
```

## PoC notes / limitations

- BERTopic's UMAP/HDBSCAN defaults assume thousands of docs; `server.py` scales
  `n_neighbors`/`n_components`/`min_cluster_size` down so short podcast samples
  cluster sensibly. Tune these for real, longer transcripts.
- `random_state=42` on UMAP makes output reproducible (at the cost of single-
  threaded embedding reduction).
- Real input will come from Phase-0 speech recognition (e.g. Whisper) producing
  the same `{start, end, text}` utterance shape.
