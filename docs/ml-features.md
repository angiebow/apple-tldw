# tldw — How the ML Features Work

Technical documentation for three of the app's AI subsystems:

1. [Virality detection](#1-virality-detection) — ranks transcript lines/scenes by viral potential
2. [Backsong (background music) generation](#2-backsong-background-music-generation) — an emotion-matched music bed per clip
3. [Blooper generation](#3-blooper-generation) — finds non-speech / dead-air spans

All three run in the local FastAPI backend (`backend/server.py`, `http://127.0.0.1:8000`), which the SwiftUI app calls over HTTP. Model weights download lazily on first use of each endpoint.

---

## 1. Virality detection

**Endpoint:** `POST /highlight` · **Code:** `backend/server.py` (`detector_scores`, `reranker_scores`, the cascade in `highlight()`) · **Weights:** `poc-content-highlighter/models/`

Virality is scored **text-only** (no audio/visual signal) using a **two-stage cascade**: a classifier *gates*, then a regressor *ranks* the survivors.

### Inputs — what gets scored

The unit of scoring ("a line") depends on how the transcript arrived:

- **Timestamped path** (from `POST /transcribe`): the transcript segments are grouped into coherent **10–60s "scenes"** by `build_scenes()` (adjacent-sentence embedding similarity + a grammatical-break guard, then a duration floor/cap). Each scene keeps a `start`/`end` so it can be cut later.
- **Pasted-text path**: the text is split into sentence-level lines by `split_lines()` (no timecodes).

Every line is tokenized at `max_length = 64` (`SCORE_MAX_LEN`) — the length the models were trained at, so **longer scenes are truncated to ~64 tokens**.

### Stage 1 — Detector (gate)

- **Model:** `distilbert-detector` — a DistilBERT **sequence classifier** (binary: viral vs. not).
- **Function:** `detector_scores()` runs the model, then:
  - `viral_prob` = `softmax(logits)[:, 1]` — P(viral) in [0, 1].
  - `viral_label` = `argmax(logits)` — the hard yes/no verdict (implicit 0.5 threshold).

### Stage 2 — Reranker (ordering)

- **Model:** `bert-ranker` — `bert-base-multilingual-cased` with a **regression head** (`num_labels=1`, `problem_type="regression"`; see `poc-content-highlighter/train_reranker.py`).
- **Function:** `reranker_scores()` outputs one continuous `viral_score` per line, trained to predict a **within-category engagement percentile (0–1)**.

### The cascade

In `highlight()`:

```python
viral_prob, viral_label = detector_scores(lines, m)   # Stage 1: gate
viral_score             = reranker_scores(lines, m)    # Stage 2: order

# Keep only detector-positive lines, ranked by the reranker's score:
viral_ranked = [i for i in np.argsort(-viral_score) if viral_label[i] == 1]
viral = [row(i) for i in viral_ranked[:k]]             # top-K → "Most viral" cards
```

So the detector **removes** everything it labels non-viral, and the reranker **sorts** what's left. The top-K become the **Most viral** cards. (The separate **Most relevant** list is unrelated to virality — it's `cosine(line, summary)` relevance ranking.)

Each result carries all three signals: `viral_prob`, `viral_label` (the seal icon on a card), and `viral_score`.

### Provenance & caveats

- Both models were trained on **YouTube trending data** (`preprocess_youtube_trending.ipynb`); the reranker regresses on **video titles** (`train_reranker.py`). At inference they're applied to **spoken transcript scenes/lines** — a real domain shift, so treat scores as approximate.
- `max_length = 64` truncates longer scenes.
- The detector and reranker are **independent** models. Only `train_reranker.py` is tracked; the detector was produced via `no3_compare_detectors.ipynb` and saved to `models/distilbert-detector`.

---

## 2. Backsong (background music) generation

**Endpoint:** `POST /backsound` · **Code:** `backend/server.py` (`_emotion_va`, `_va_to_music_prompt`, `_generate_music`) · **Models:** `j-hartmann/emotion-english-distilroberta-base` + `facebook/musicgen-small`

Generates an **emotion-matched instrumental music bed** for a clip, from the clip's text. The pipeline is: **text → emotion → (valence, arousal) → text prompt → MusicGen audio**.

### Step 1 — Emotion classification → (valence, arousal)

`_emotion_va()` runs the DistilRoBERTa emotion classifier (7 classes: anger, disgust, fear, joy, neutral, sadness, surprise) and takes the **softmax-weighted average** of each emotion's coordinate on **Russell's circumplex** — a `(valence, arousal)` point in [-1, 1]:

```python
_EMOTION_VA = {
    "anger":   (-0.6, 0.8),  "disgust": (-0.6, 0.4), "fear": (-0.7, 0.8),
    "joy":     ( 0.8, 0.6),  "neutral": ( 0.0, 0.0), "sadness": (-0.7, -0.4),
    "surprise":( 0.4, 0.7),
}
```

It also returns the dominant emotion and the full per-emotion score map (shown in the UI).

### Step 2 — (valence, arousal) → MusicGen prompt

`_va_to_music_prompt()` translates the point into musical adjectives:

- **Mode** from valence: `major key` (≥ 0.15) / `minor key` (≤ -0.15) / `modal` (in between).
- **Tempo** from arousal: `~120 BPM energetic` (≥ 0.5) / `~90 BPM moderate` / `~60 BPM slow, ambient` (≤ -0.1).
- **Mood** from the quadrant: e.g. bright/uplifting (high valence+arousal), warm/gentle (positive, calm), dark/tense/dissonant (negative, aroused), somber/melancholic (negative, calm), with a neutral dead-zone.

Result, e.g.: `"bright, uplifting, hopeful instrumental background music, major key, energetic and driving, around 120 BPM, cinematic underscore, no vocals"`.

### Step 3 — MusicGen synthesis

`_generate_music()` runs `facebook/musicgen-small` (overridable via `TLDW_MUSICGEN`):

- Length: `duration_s` if provided, else `_estimate_seconds(text)` (~2.5 words/sec), **clamped to [2, 15] seconds** (`MUSIC_MAX_SECONDS`). Token budget = `seconds × 50` (`MUSICGEN_TOK_RATE`).
- Sampling: `do_sample=True`, `guidance_scale=3.0`.
- Output is mono float, peak-normalized to 0.9 headroom, encoded to **16-bit PCM WAV** and returned **base64** (`audio_b64`), with `sample_rate`, `duration_s`, the prompt, dominant `emotion`, and `valence`/`arousal`.

### How the bed is used in the app

The base64 WAV is stored per clip. In the editor it's **looped and synced** under the clip preview at a user-set volume, shown as a tile in the **Backsound** timeline lane. On export it's mixed into the video by ffmpeg (see `POST /clip` / `POST /merge`: each `ClipSpan` can carry `music_b64` + `music_volume`, mixed under the speech with `amix ... normalize=0`).

---

## 3. Blooper generation

**Endpoint:** `POST /bloopers` · **Code:** `poc-blooper-detector/blooper.py` (`detect`) · **Models:** Silero VAD (via `torch.hub`) + OpenCV Haar cascade

A "blooper" here means **any non-speech span** — silence, pauses, dead air, mic drops, cutaways. Detection is **audio-first**; the visual lip check only *labels* spans, it never creates them.

### Step 1 — Extract audio

`extract_audio()` uses ffmpeg to decode the video to a **16 kHz mono WAV** (what Silero VAD expects).

### Step 2 — Find speech (Silero VAD)

`speech_spans()` runs **Silero VAD** (`snakers4/silero-vad` from `torch.hub`) and returns the timestamped intervals where someone is talking.

### Step 3 — Invert speech → non-speech spans

`nonspeech_spans()` is pure interval math over `[0, total_duration]`:

1. Merge speech intervals closer than `merge_gap` (0.3s) — so VAD flicker doesn't shatter one pause into many.
2. Take the gaps between merged speech.
3. Trim `pad` (0.1s) off each gap end — don't clip the surrounding words.
4. Drop gaps shorter than `min_dur`.

> **Note:** the app's `/bloopers` call uses `min_dur = 0.0` — **no duration floor**, so every non-speech gap becomes a candidate (hence the many short spans on the timeline).

### Step 4 — Visual lip check (optional labeling)

`lip_motion_score()` is a best-effort **OpenCV Haar** check (not MediaPipe — it lacks reliable macOS-arm64 wheels). For sampled frames of the span it finds the largest face, crops the **mouth region** (lower third of the face box, central 60% width), and measures the **mean frame-to-frame pixel difference** of that crop, normalized to [0, 1].

The score sets each span's `label` (threshold `lip_threshold = 0.04`):

- **`silent`** — mouth static → genuinely not speaking (true dead air, safe to trim).
- **`lips_moving`** — mouth moving while audio is silent → someone's mouthing but audio dropped (e.g. a dropped mic); arguably *not* dead air.
- **`no_face`** — no face found / OpenCV unavailable → the audio-only verdict stands.

The lip check is advisory: it adds a confidence signal on *what kind* of non-speech a span is, but **VAD alone decides where the spans are**. Nothing currently filters on the label (e.g. `lips_moving` spans are still listed).

### Output

`detect()` returns a list of `Blooper(start, end, duration, lip_motion, label)`. `/bloopers` returns these as JSON; the app drops them onto the **Bloopers** timeline lane (red tiles labeled `silent` / `lips` / `no face`), where they can be previewed and selected for merging.

---

## Quick reference

| Feature | Endpoint | Core models | Signal used |
|---|---|---|---|
| Virality | `POST /highlight` | DistilBERT detector + BERT reranker | Text only (cascade: gate → rank) |
| Backsong | `POST /backsound` | DistilRoBERTa emotion + MusicGen | Text → emotion → music |
| Bloopers | `POST /bloopers` | Silero VAD + OpenCV Haar | Audio (spans) + video (labels) |
