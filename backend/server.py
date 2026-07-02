"""tldw — Transcript Content Highlighter sidecar.

A FastAPI server that runs the 5-step highlighter pipeline and returns two
top-K ranked lists for a transcript:

    1. Summarize the transcript                 Llama-3.2-3B-Instruct (MLX)
    2. Embed each line + the summary            sentence-transformers/all-mpnet-base-v2
    3. relevance = cosine(line, summary)        (most-relevant list)
    4. Virality Detector (classification)       distilbert  -> viral_prob / viral_label
    5. Virality Reranker (regression)           bert-base   -> viral_score (most-viral list)

The SwiftUI app talks to this over http://127.0.0.1:8000.

Run:  ./setup.sh && ./run.sh
"""
from __future__ import annotations

import base64
import io
import os
import re
import sys
import wave
from typing import Optional

import numpy as np
import torch
import torch.nn.functional as F
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                          AutoProcessor, MusicgenForConditionalGeneration)

# ── Config ────────────────────────────────────────────────────────────────
DEVICE = ("cuda" if torch.cuda.is_available()
          else "mps" if torch.backends.mps.is_available() else "cpu")

MODELS_DIR = os.environ.get(
    "TLDW_MODELS_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "poc-content-highlighter", "models"),
)
# Summarizer: instruction-tuned Llama 3.2 3B (4-bit, MLX) — ported from the
# poc-audio-extraction LLM pipeline. Instruction-tuned so a supplied video title
# can be injected into the prompt to steer the summary (and the relevance ranking
# derived from it). Apple-Silicon-only (mlx_lm); overridable for other MLX repos.
SUMMARIZER_NAME = os.environ.get("TLDW_SUMMARIZER",
                                 "mlx-community/Llama-3.2-3B-Instruct-4bit")
EMBEDDER_NAME   = "sentence-transformers/all-mpnet-base-v2"
DETECTOR_DIR    = os.path.join(MODELS_DIR, "distilbert-detector")
RERANKER_DIR    = os.path.join(MODELS_DIR, "bert-ranker")

SUMM_CHUNK_TOKENS = 1024  # pack transcript into <=1024-token chunks before summarizing
SCORE_MAX_LEN   = 64    # detector/reranker were trained at max_length=64
MIN_LINE_WORDS  = 4     # drop trivially short lines

# Scene builder (matches the poc-audio-extraction pipeline): group transcript
# segments into semantically + grammatically coherent scenes, floored/capped in
# duration. These become the "Shorts" that get ranked and cut.
SCENE_MIN_SECONDS = float(os.environ.get("TLDW_SCENE_MIN_SECONDS", "10.0"))
SCENE_MAX_SECONDS = float(os.environ.get("TLDW_SCENE_MAX_SECONDS", "60.0"))

# Backsound: sentiment -> valence/arousal -> MusicGen prompt -> music bed.
EMOTION_NAME      = "j-hartmann/emotion-english-distilroberta-base"
MUSICGEN_NAME     = os.environ.get("TLDW_MUSICGEN", "facebook/musicgen-small")
MUSIC_DEVICE      = os.environ.get("TLDW_MUSIC_DEVICE", DEVICE)  # set =cpu if MPS misbehaves
MUSIC_MAX_SECONDS = 15.0
MUSICGEN_TOK_RATE = 50                   # MusicGen emits ~50 audio tokens / second

# Bloopers: Silero VAD inverts speech → non-speech ("blooper") spans, with an
# optional OpenCV lip-motion check. The pipeline lives in the PoC module.
POC_BLOOPER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "poc-blooper-detector")

# Transcription: a raw video/audio file → preprocessed 16 kHz WAV + VAD → Whisper
# STT → transcript text (which then feeds /highlight). Pipeline lives in the PoC.
POC_AUDIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "poc-audio-extraction")
# Single-pass MLX Whisper (hans-development). "turbo" → whisper-large-v3-turbo;
# also accepts sizes ("base", "small", …) or a full mlx-community repo id.
WHISPER_MODEL = os.environ.get("TLDW_WHISPER_MODEL", "turbo")

# Clipping: cut the ranked lines' spans out of the source video into .mp4 files.
POC_CLIPPER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "poc-video-clipper")
# Subtitles: soft .srt sidecars written next to each exported clip (hans-development).
POC_SUBTITLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "poc-subtitles")
# Where exported clips land (per-source subfolder is added at request time).
CLIPS_OUT_DIR = os.environ.get(
    "TLDW_CLIPS_DIR", os.path.expanduser("~/Movies/tldw-clips"))

# ── Lazy-loaded model singletons ─────────────────────────────────────────
_models = {}


def _load_models():
    if _models:
        return _models
    print(f"[tldw] loading models on {DEVICE} …")
    # Llama summarizer via MLX (Apple Silicon). Imported here (not at module top)
    # so the dependency is only required when models actually load.
    from mlx_lm import load as _mlx_load  # noqa: E402  (intentionally deferred)
    _models["summarizer"], _models["summarizer_tok"] = _mlx_load(SUMMARIZER_NAME)
    _models["embedder"] = SentenceTransformer(EMBEDDER_NAME, device=DEVICE)

    _models["detector_tok"] = AutoTokenizer.from_pretrained(DETECTOR_DIR)
    _models["detector"] = AutoModelForSequenceClassification.from_pretrained(DETECTOR_DIR).to(DEVICE).eval()

    _models["reranker_tok"] = AutoTokenizer.from_pretrained(RERANKER_DIR)
    _models["reranker"] = AutoModelForSequenceClassification.from_pretrained(RERANKER_DIR).to(DEVICE).eval()
    print("[tldw] models ready")
    return _models


# ── Pipeline pieces ────────────────────────────────────────────────────────
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_lines(text: str) -> list[str]:
    """Break a transcript into candidate lines: explicit line breaks first,
    otherwise sentence splits. Drop trivially short fragments."""
    raw = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(raw) <= 1:  # no real line breaks -> split into sentences
        raw = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    return [ln for ln in raw if len(ln.split()) >= MIN_LINE_WORDS]


def _sentence_chunks(text: str, tok, budget: int) -> list[str]:
    """Pack whole sentences into chunks of <= budget tokens (greedy), so no
    sentence is split across a chunk boundary. Uses the Llama tokenizer's
    encode/decode (MLX TokenizerWrapper proxies these to the HF tokenizer)."""
    sents = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]

    chunks, cur, cur_len = [], [], 0
    for s in sents:
        n = len(tok.encode(s, add_special_tokens=False))
        # A single sentence longer than the budget can't fit whole anywhere;
        # hard-split just that one by tokens (unavoidable mid-sentence cut).
        if n > budget:
            if cur:
                chunks.append(" ".join(cur)); cur, cur_len = [], 0
            ids = tok.encode(s, add_special_tokens=False)
            for i in range(0, len(ids), budget):
                chunks.append(tok.decode(ids[i:i + budget]))
            continue
        # Flush before adding the sentence that would overflow the budget.
        if cur_len + n > budget:
            chunks.append(" ".join(cur)); cur, cur_len = [], 0
        cur.append(s); cur_len += n

    if cur:
        chunks.append(" ".join(cur))
    return chunks


def _llama_generate(m, instruction: str, max_tokens: int) -> str:
    """Run one instruction through the MLX Llama chat model and return the text."""
    from mlx_lm import generate  # noqa: E402  (deferred with the model load)
    model, tok = m["summarizer"], m["summarizer_tok"]
    messages = [{"role": "user", "content": instruction}]
    prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    return generate(model, tok, prompt=prompt, max_tokens=max_tokens, verbose=False).strip()


def summarize(text: str, m, title: str = "") -> str:
    """Summarize the transcript with the instruction-tuned Llama summarizer, using
    the same map-reduce as the poc-audio-extraction pipeline: summarize each
    <=1024-token chunk, then synthesize a cohesive global summary. A supplied
    `title` is injected into both prompts so the summary — and the relevance
    ranking derived from it — stays anchored to what the video is about."""
    tok = m["summarizer_tok"]
    title = (title or "").strip()
    title_ctx = f'The video is titled "{title}". ' if title else ""

    # Map: one summary per chunk.
    chunk_summaries = []
    for chunk_text in _sentence_chunks(text, tok, SUMM_CHUNK_TOKENS):
        prompt = (
            "You are a professional editor. " + title_ctx
            + "Meticulously summarize the following transcript chunk. "
            "Do NOT assume or guess the names of any speakers. "
            "Start the summary directly with the key points. Limit introduction phrases.\n\n"
            f"Transcript:\n{chunk_text}\n\nSummary:"
        )
        chunk_summaries.append(_llama_generate(m, prompt, max_tokens=150))

    if not chunk_summaries:
        return ""

    # Reduce: synthesize a cohesive global summary from the chunk summaries.
    combined = " ".join(chunk_summaries)
    prompt = (
        title_ctx
        + "Synthesize a cohesive global summary based on these chunk summaries. "
        "Limit to exactly 2-3 sentences.\n\n"
        f"Summaries:\n{combined}\n\nGlobal Summary:"
    )
    return _llama_generate(m, prompt, max_tokens=256)


@torch.no_grad()
def relevance_scores(lines: list[str], summary: str, m) -> np.ndarray:
    emb = m["embedder"].encode([summary] + lines, normalize_embeddings=True,
                               convert_to_numpy=True)
    summary_vec, line_vecs = emb[0], emb[1:]
    return line_vecs @ summary_vec  # cosine (vectors are normalized)


@torch.no_grad()
def detector_scores(lines: list[str], m) -> tuple[np.ndarray, np.ndarray]:
    tok, model = m["detector_tok"], m["detector"]
    # DistilBERT's forward() has no `token_type_ids` arg, so don't emit them.
    enc = tok(lines, return_tensors="pt", truncation=True, padding=True,
              max_length=SCORE_MAX_LEN, return_token_type_ids=False).to(DEVICE)
    logits = model(**enc).logits
    probs = F.softmax(logits, dim=-1)[:, 1]
    labels = logits.argmax(-1)
    return probs.cpu().numpy(), labels.cpu().numpy()


@torch.no_grad()
def reranker_scores(lines: list[str], m) -> np.ndarray:
    tok, model = m["reranker_tok"], m["reranker"]
    enc = tok(lines, return_tensors="pt", truncation=True, padding=True,
              max_length=SCORE_MAX_LEN).to(DEVICE)
    return model(**enc).logits.squeeze(-1).cpu().numpy()


# ── Scene builder (matches the poc-audio-extraction pipeline) ──────────────────
# Group consecutive transcript segments into coherent "scenes" (the Shorts we
# rank + cut), using the same logic as poc-video-clipper/run_llm_pipeline.py:
#   1. Embed each segment; take adjacent cosine similarities.
#   2. Cut a new scene at a boundary only when similarity dips below
#      (mean − 0.5·std) AND it's a grammatical break (prev ends .?!, next is
#      capitalised) — a "grammatical guard" so we never split mid-sentence.
#   3. Recursively split any scene longer than SCENE_MAX_SECONDS at its weakest
#      grammatical boundary (or weakest boundary overall if none qualify).
#   4. Drop scenes shorter than SCENE_MIN_SECONDS.
# Embeddings are unit-normalised, so cosine similarity is just a dot product.
def _split_scene_recursive(scene: list[dict], emb: dict, max_duration: float) -> list[list[dict]]:
    duration = scene[-1]["end"] - scene[0]["start"]
    if duration <= max_duration or len(scene) <= 1:
        return [scene]

    # Prefer grammatical break points; fall back to every boundary.
    grammatical = []
    for i in range(len(scene) - 1):
        a, b = scene[i]["text"].strip(), scene[i + 1]["text"].strip()
        if a and a[-1] in ".?!" and b and b[0].isupper():
            grammatical.append(i)
    candidates = grammatical or list(range(len(scene) - 1))

    lowest_sim, split_idx = float("inf"), -1
    for i in candidates:
        sim = float(np.dot(emb[scene[i]["id"]], emb[scene[i + 1]["id"]]))
        if sim < lowest_sim:
            lowest_sim, split_idx = sim, i
    if split_idx == -1:
        return [scene]

    left, right = scene[:split_idx + 1], scene[split_idx + 1:]
    return (_split_scene_recursive(left, emb, max_duration)
            + _split_scene_recursive(right, emb, max_duration))


def build_scenes(segments, m,
                 min_duration: float = SCENE_MIN_SECONDS,
                 max_duration: float = SCENE_MAX_SECONDS) -> list[dict]:
    """Build 10–60s scenes from timestamped transcript segments (see above)."""
    segs = [{"id": i, "start": float(s.start), "end": float(s.end), "text": s.text}
            for i, s in enumerate(segments)]
    if not segs:
        return []

    emb_arr = m["embedder"].encode([s["text"] for s in segs],
                                   normalize_embeddings=True, convert_to_numpy=True)
    emb = {segs[i]["id"]: emb_arr[i] for i in range(len(segs))}

    if len(segs) == 1:
        groups = [segs]
    else:
        sims = np.array([float(np.dot(emb_arr[i], emb_arr[i + 1]))
                         for i in range(len(segs) - 1)])
        threshold = float(sims.mean() - 0.5 * sims.std())
        groups, current = [], [segs[0]]
        for i in range(len(sims)):
            a, b = segs[i]["text"].strip(), segs[i + 1]["text"].strip()
            ends_punc = bool(a) and a[-1] in ".?!"
            next_cap = bool(b) and b[0].isupper()
            if sims[i] < threshold and ends_punc and next_cap:
                groups.append(current)
                current = [segs[i + 1]]
            else:
                current.append(segs[i + 1])
        groups.append(current)

    scenes = []
    for group in groups:
        for sg in _split_scene_recursive(group, emb, max_duration):
            start, end = sg[0]["start"], sg[-1]["end"]
            if end - start < min_duration:  # floor: no sub-10s scenes
                continue
            scenes.append({"start": round(start, 2), "end": round(end, 2),
                           "text": " ".join(s["text"] for s in sg)})
    return scenes


# ── Audio helpers (shared by the backsound endpoint) ───────────────────────
def _estimate_seconds(text: str) -> float:
    """Spoken duration at ~2.5 words/sec — matches the SwiftUI card estimate."""
    return max(1.0, round(len(text.split()) / 2.5))


def _to_wav_bytes(signal: np.ndarray, sr: int) -> bytes:
    """Mono float[-1,1] -> 16-bit PCM WAV bytes."""
    pcm = (np.clip(signal, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


# ── Backsound (sentiment -> MusicGen music bed) ────────────────────────────
# Each emotion sits at a (valence, arousal) point in [-1, 1] — Russell's
# circumplex. We take the softmax-weighted average, then translate that point
# into musical adjectives/mode/tempo for the MusicGen prompt.
_EMOTION_VA = {
    "anger":    (-0.6,  0.8),
    "disgust":  (-0.6,  0.4),
    "fear":     (-0.7,  0.8),
    "joy":      ( 0.8,  0.6),
    "neutral":  ( 0.0,  0.0),
    "sadness":  (-0.7, -0.4),
    "surprise": ( 0.4,  0.7),
}

# Lazily loaded so the heavy MusicGen weights download only on first /backsound.
_music = {}


def _load_music():
    if _music:
        return _music
    print(f"[tldw] loading emotion + MusicGen ({MUSICGEN_NAME}) on {MUSIC_DEVICE} …")
    _music["emo_tok"] = AutoTokenizer.from_pretrained(EMOTION_NAME)
    _music["emo"] = AutoModelForSequenceClassification.from_pretrained(EMOTION_NAME).to(MUSIC_DEVICE).eval()
    _music["proc"] = AutoProcessor.from_pretrained(MUSICGEN_NAME)
    _music["gen"] = MusicgenForConditionalGeneration.from_pretrained(MUSICGEN_NAME).to(MUSIC_DEVICE).eval()
    print("[tldw] MusicGen ready")
    return _music


@torch.no_grad()
def _emotion_va(text: str, m) -> tuple[float, float, str, dict]:
    """Classify emotion and reduce to a (valence, arousal) point."""
    tok, model = m["emo_tok"], m["emo"]
    enc = tok(text, return_tensors="pt", truncation=True, max_length=128).to(MUSIC_DEVICE)
    probs = F.softmax(model(**enc).logits, dim=-1)[0].cpu().numpy()
    id2label = model.config.id2label

    val = aro = 0.0
    scores = {}
    for i, p in enumerate(probs):
        label = id2label[i].lower()
        v, a = _EMOTION_VA.get(label, (0.0, 0.0))
        val += p * v
        aro += p * a
        scores[label] = round(float(p), 3)
    dominant = id2label[int(probs.argmax())].lower()
    return float(val), float(aro), dominant, scores


def _va_to_music_prompt(valence: float, arousal: float) -> str:
    """Map a (valence, arousal) point to a MusicGen text prompt."""
    mode = ("major key" if valence >= 0.15
            else "minor key" if valence <= -0.15 else "modal")
    if arousal >= 0.5:
        tempo = "energetic and driving, around 120 BPM"
    elif arousal <= -0.1:
        tempo = "slow and sparse, around 60 BPM, ambient"
    else:
        tempo = "moderate tempo, around 90 BPM"
    # Quadrant adjectives (valence × arousal), with a neutral dead-zone center.
    if abs(valence) < 0.15 and abs(arousal) < 0.2:
        mood = "calm, neutral, understated"
    elif valence >= 0 and arousal >= 0:
        mood = "bright, uplifting, hopeful"
    elif valence >= 0:
        mood = "warm, gentle, peaceful"
    elif arousal >= 0:
        mood = "dark, tense, suspenseful, dissonant"
    else:
        mood = "somber, melancholic, hollow"
    return (f"{mood} instrumental background music, {mode}, {tempo}, "
            f"cinematic underscore, no vocals")


def _generate_music(prompt: str, seconds: float, m) -> tuple[bytes, int]:
    """Run MusicGen and return (WAV bytes, sample_rate)."""
    proc, gen = m["proc"], m["gen"]
    inputs = proc(text=[prompt], padding=True, return_tensors="pt").to(MUSIC_DEVICE)
    sr = gen.config.audio_encoder.sampling_rate
    max_new = int(seconds * MUSICGEN_TOK_RATE)
    with torch.no_grad():
        audio = gen.generate(**inputs, do_sample=True, guidance_scale=3.0,
                             max_new_tokens=max_new)
    wav = audio[0, 0].cpu().numpy().astype("float32")     # mono float [-1, 1]
    wav = wav / (np.max(np.abs(wav)) + 1e-9) * 0.9        # normalize headroom
    return _to_wav_bytes(wav, sr), sr


# ── Bloopers (Silero VAD → non-speech spans) ───────────────────────────────
# The detection pipeline lives in poc-blooper-detector/blooper.py. We import it
# lazily and cache the module: its top level only needs numpy, but Silero VAD
# (torch.hub) and OpenCV are pulled in on the first detect() call, so /highlight
# startup is unaffected.
_blooper = {}


def _load_blooper():
    if "mod" not in _blooper:
        if POC_BLOOPER_DIR not in sys.path:
            sys.path.insert(0, POC_BLOOPER_DIR)
        import blooper  # noqa: E402  (intentionally deferred)
        _blooper["mod"] = blooper
    return _blooper["mod"]


# ── Transcription (audio_preprocessor → VAD-guided Whisper) ─────────────────
# Same deferred-import trick as the blooper module: the transcription pipeline
# only pulls in torchaudio + Whisper on the first /transcribe call, so /highlight
# startup stays fast. Whisper weights download on that first call.
_transcriber = {}


def _load_transcriber():
    if "mod" not in _transcriber:
        if POC_AUDIO_DIR not in sys.path:
            sys.path.insert(0, POC_AUDIO_DIR)
        import transcribe_pipeline  # noqa: E402  (intentionally deferred)
        _transcriber["mod"] = transcribe_pipeline
    return _transcriber["mod"]


# ── Clipping (cut ranked spans → .mp4 files) ────────────────────────────────
_clipper = {}


def _load_clipper():
    if "mod" not in _clipper:
        if POC_CLIPPER_DIR not in sys.path:
            sys.path.insert(0, POC_CLIPPER_DIR)
        import cut_viral_clips  # noqa: E402  (intentionally deferred)
        _clipper["mod"] = cut_viral_clips
    return _clipper["mod"]


# ── Subtitles (soft .srt sidecars, hans-development poc-subtitles) ───────────
_subs = {}


def _load_subtitles():
    if "mod" not in _subs:
        if POC_SUBTITLES_DIR not in sys.path:
            sys.path.insert(0, POC_SUBTITLES_DIR)
        import subtitles  # noqa: E402  (intentionally deferred)
        _subs["mod"] = subtitles
    return _subs["mod"]


# ── API ──────────────────────────────────────────────────────────────────
app = FastAPI(title="tldw highlighter")


class WordIn(BaseModel):
    word: str
    start: float
    end: float


class TranscriptSegmentIn(BaseModel):
    text: str
    start: float
    end: float
    words: Optional[list[WordIn]] = None   # per-word times → karaoke subtitles


class HighlightRequest(BaseModel):
    text: str
    top_k: int = 10
    # Optional user-supplied video title. Folded into summarization so the summary
    # (and the relevance ranking derived from it) stays anchored to the topic.
    title: Optional[str] = None
    # Optional timestamped lines from /transcribe. When present, these are ranked
    # directly (instead of re-splitting `text`) so each result carries start/end —
    # which the /clip endpoint needs to cut the actual video span.
    segments: Optional[list[TranscriptSegmentIn]] = None


class BacksoundRequest(BaseModel):
    text: str                             # line (or whole-Short text) to read mood from
    duration_s: Optional[int] = None      # bed length; defaults to a spoken estimate


class BlooperRequest(BaseModel):
    video_path: str                       # absolute path to the source video (local)
    use_lip_check: bool = True            # confirm a still mouth with the visual check


class TranscribeRequest(BaseModel):
    video_path: str                       # absolute path to a local video/audio file
    model: Optional[str] = None           # Whisper size; defaults to TLDW_WHISPER_MODEL


class ClipSpan(BaseModel):
    start: float
    end: float
    text: str = ""
    # Optional per-clip background music bed (base64 WAV from /backsound), mixed
    # under the speech at `music_volume` (0–1) when present.
    music_b64: Optional[str] = None
    music_volume: float = 0.35


class ClipRequest(BaseModel):
    video_path: str                       # absolute path to the source video (local)
    clips: list[ClipSpan]                 # spans to cut, in order
    name: Optional[str] = None            # output subfolder name (defaults to the source stem)
    vertical: bool = True                 # render 1080×1920 portrait Shorts
    subtitles: bool = True                # burn karaoke captions (needs ffmpeg libass)
    srt: bool = True                      # also write a soft .srt sidecar per clip
    # Full transcript segments with per-word times; the endpoint slices the words
    # falling inside each clip span to caption it. Optional (no words → no captions).
    segments: Optional[list[TranscriptSegmentIn]] = None


class MergeRequest(BaseModel):
    video_path: str                       # absolute path to the source video (local)
    clips: list[ClipSpan]                 # spans to concatenate into one Short, in order
    name: Optional[str] = None            # output file stem (defaults to <source>_merged)
    vertical: bool = True                 # render 1080×1920 portrait
    subtitles: bool = True                # burn karaoke captions (needs ffmpeg libass)
    # Full transcript segments with per-word times; words overlapping each span
    # caption it. Optional (no words → no captions).
    segments: Optional[list[TranscriptSegmentIn]] = None


@app.get("/health")
def health():
    return {"status": "ok", "device": DEVICE}


@app.post("/highlight")
def highlight(req: HighlightRequest):
    text = req.text.strip()
    if len(text) < 40:
        raise HTTPException(status_code=400, detail="Transcript too short (need ~40+ characters).")

    m = _load_models()

    # Timestamped path (from /transcribe): group segments into 10–60s scenes — the
    # poc-audio-extraction behavior — so each ranked Short keeps a start/end span.
    # Text path (pasted transcript): re-split into lines as before, no timestamps.
    if req.segments:
        scenes = build_scenes(req.segments, m)
        lines = [sc["text"] for sc in scenes]
        times = [(sc["start"], sc["end"]) for sc in scenes]
        if not lines:
            raise HTTPException(
                status_code=400,
                detail=(f"No scenes of at least {SCENE_MIN_SECONDS:.0f}s could be formed "
                        f"from the transcript — the recording may be too short."))
    else:
        lines = split_lines(text)
        times = None
        if not lines:
            raise HTTPException(status_code=400,
                                detail="Could not extract any usable lines from the transcript.")

    summary = summarize(text, m, title=req.title or "")

    relevance = relevance_scores(lines, summary, m)
    viral_prob, viral_label = detector_scores(lines, m)
    viral_score = reranker_scores(lines, m)

    def row(i: int) -> dict:
        i = int(i)  # argsort yields np.int64; cast for JSON serialization
        r = {"index": i, "text": lines[i],
             "relevance": round(float(relevance[i]), 4),
             "viral_score": round(float(viral_score[i]), 4),
             "viral_prob": round(float(viral_prob[i]), 4),
             "viral_label": bool(viral_label[i])}
        if times is not None:
            r["start"] = round(float(times[i][0]), 3)
            r["end"] = round(float(times[i][1]), 3)
        return r

    k = max(1, min(req.top_k, len(lines)))
    # Relevant list: all lines ranked by similarity to the summary.
    relevant = [row(i) for i in np.argsort(-relevance)[:k]]
    # Viral list: cascade — the detector first gates out non-viral lines, then
    # the reranker scores/orders only the survivors.
    viral_ranked = [i for i in np.argsort(-viral_score) if viral_label[i] == 1]
    viral = [row(i) for i in viral_ranked[:k]]

    return {
        "summary": summary,
        "models": {"summarizer": SUMMARIZER_NAME, "embedder": EMBEDDER_NAME,
                   "detector": "distilbert-detector", "reranker": "bert-ranker"},
        "line_count": len(lines),
        "relevant": relevant,
        "viral": viral,
    }


@app.post("/backsound")
def backsound(req: BacksoundRequest):
    """Generate an emotion-matched background music bed for a clip (editor)."""
    text = req.text.strip()
    if len(text) < 4:
        raise HTTPException(status_code=400, detail="Text too short to read a mood from.")

    seconds = float(req.duration_s) if req.duration_s else _estimate_seconds(text)
    seconds = max(2.0, min(seconds, MUSIC_MAX_SECONDS))

    m = _load_music()
    valence, arousal, emotion, scores = _emotion_va(text, m)
    prompt = _va_to_music_prompt(valence, arousal)
    wav, sr = _generate_music(prompt, seconds, m)

    return {
        "prompt": prompt,
        "emotion": emotion,
        "valence": round(valence, 3),
        "arousal": round(arousal, 3),
        "scores": scores,
        "audio_b64": base64.b64encode(wav).decode("ascii"),
        "sample_rate": sr,
        "duration_s": round(seconds, 2),
        "model": MUSICGEN_NAME,
    }


@app.post("/bloopers")
def bloopers(req: BlooperRequest):
    """Find non-speech 'blooper' spans (silence / pauses / dead air) in a video.

    The macOS app picks the source video and sends its local path; the app then
    previews each span by seeking the source video, so we only return span
    metadata here (no clip cutting)."""
    path = os.path.expanduser(req.video_path.strip())
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=400, detail=f"Video not found: {req.video_path}")

    mod = _load_blooper()
    try:
        # min_dur=0: any non-speech span counts as a blooper, no duration floor.
        # (merge_gap / pad stay at the module defaults — VAD-flicker smoothing and
        # edge padding so we don't fragment one pause or clip the surrounding words.)
        spans = mod.detect(
            path,
            min_dur=0.0,
            use_lip_check=req.use_lip_check,
        )
        duration = mod.video_duration(path)
    except Exception as exc:  # ffmpeg / VAD / decode failures → 500 with the reason
        raise HTTPException(status_code=500, detail=f"Blooper detection failed: {exc}")

    return {
        "source": os.path.basename(path),
        "duration_s": round(duration, 3),
        "count": len(spans),
        "params": {"lip_check": req.use_lip_check},
        "bloopers": [
            {
                "index": i,
                "start": b.start,
                "end": b.end,
                "duration": b.duration,
                "lip_motion": b.lip_motion,
                "label": b.label,
            }
            for i, b in enumerate(spans)
        ],
    }


@app.post("/transcribe")
def transcribe(req: TranscribeRequest):
    """Turn a raw local video/audio file into transcript text.

    Runs the audio_preprocessor pipeline (extract → denoise → normalise → VAD)
    then VAD-guided Whisper STT. The macOS app picks the file and sends its local
    path (app + backend share disk); the returned text drops straight into the
    highlighter's transcript field."""
    path = os.path.expanduser(req.video_path.strip())
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=400, detail=f"File not found: {req.video_path}")

    mod = _load_transcriber()
    model_name = (req.model or WHISPER_MODEL).strip()
    try:
        result = mod.transcribe(path, model_name=model_name)
    except Exception as exc:  # ffmpeg / preprocessing / Whisper failures → 500
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}")

    return {
        "source": os.path.basename(path),
        "duration_s": result["duration_s"],
        "speech_ratio": result["speech_ratio"],
        "model": result["model"],
        "text": result["text"],
        "segments": result["segments"],
    }


@app.post("/clip")
def clip(req: ClipRequest):
    """Cut the given spans out of the source video into standalone .mp4 files.

    The app sends the source video path plus the selected Shorts' timecodes; each
    span is re-encoded into its own clip under CLIPS_OUT_DIR/<name>/. Returns the
    output folder and per-clip paths so the app can reveal them in Finder."""
    path = os.path.expanduser(req.video_path.strip())
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=400, detail=f"Source video not found: {req.video_path}")

    # Flatten all transcript words; each clip is captioned with the words that
    # overlap its span (source timeline).
    all_words = [{"word": w.word, "start": w.start, "end": w.end}
                 for seg in (req.segments or []) for w in (seg.words or [])]

    def words_in(a: float, b: float) -> list:
        return [w for w in all_words if w["start"] < b and w["end"] > a]

    spans = [{"start": c.start, "end": c.end, "text": c.text, "words": words_in(c.start, c.end),
              "music_b64": c.music_b64, "music_volume": c.music_volume}
             for c in req.clips if c.end > c.start]
    if not spans:
        raise HTTPException(status_code=400, detail="No valid clip spans to cut.")

    stem = os.path.splitext(os.path.basename(path))[0]
    name = (req.name or stem).strip() or stem
    out_dir = os.path.join(CLIPS_OUT_DIR, name)

    mod = _load_clipper()
    subtitles_applied = req.subtitles and mod.ffmpeg_has_subtitles()
    try:
        written = mod.cut_clips(path, spans, out_dir,
                                vertical=req.vertical, subtitles=req.subtitles)
    except Exception as exc:  # ffmpeg failure / bad path → 500 with the reason
        detail = getattr(exc, "stderr", None) or str(exc)
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        raise HTTPException(status_code=500, detail=f"Clip cutting failed: {detail}")

    # Soft .srt sidecar next to each clip (clip-relative cues from the words that
    # fall inside the span). Non-fatal: the .mp4s already exist if this trips.
    srt_paths: dict = {}
    if req.srt:
        try:
            subs = _load_subtitles()
            for p, s in zip(written, spans):
                clip_words = subs.rebase_words(s["words"], s["start"], s["end"])
                cues = subs.build_cues(clip_words) if clip_words else []
                if not cues:
                    continue
                srt_path = os.path.splitext(p)[0] + ".srt"
                subs.write_srt(cues, srt_path)
                srt_paths[p] = srt_path
        except Exception as exc:  # keep the clips; just skip captions
            print(f"[tldw] .srt sidecar generation failed: {exc}")

    return {
        "source": os.path.basename(path),
        "output_dir": out_dir,
        "count": len(written),
        "vertical": req.vertical,
        # True only if captions were actually burned (requested AND ffmpeg has libass).
        "subtitles_applied": subtitles_applied,
        "subtitles_requested": req.subtitles,
        "srt_count": len(srt_paths),
        "clips": [
            {"index": i, "path": p, "start": s["start"], "end": s["end"], "text": s["text"],
             "srt": srt_paths.get(p)}
            for i, (p, s) in enumerate(zip(written, spans))
        ],
    }


@app.post("/merge")
def merge(req: MergeRequest):
    """Render the given spans as portrait+captioned segments and concatenate them
    into one merged Short (.mp4).

    Same span/word handling as /clip, but instead of returning per-clip files it
    stitches the segments into a single video and returns that path so the app can
    save / reveal it."""
    path = os.path.expanduser(req.video_path.strip())
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=400, detail=f"Source video not found: {req.video_path}")

    # Flatten transcript words; each span is captioned with the words overlapping it.
    all_words = [{"word": w.word, "start": w.start, "end": w.end}
                 for seg in (req.segments or []) for w in (seg.words or [])]

    def words_in(a: float, b: float) -> list:
        return [w for w in all_words if w["start"] < b and w["end"] > a]

    spans = [{"start": c.start, "end": c.end, "text": c.text, "words": words_in(c.start, c.end),
              "music_b64": c.music_b64, "music_volume": c.music_volume}
             for c in req.clips if c.end > c.start]
    if not spans:
        raise HTTPException(status_code=400, detail="No valid clip spans to merge.")

    stem = os.path.splitext(os.path.basename(path))[0]
    name = (req.name or f"{stem}_merged").strip() or f"{stem}_merged"
    out_dir = os.path.join(CLIPS_OUT_DIR, name)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{name}.mp4")

    mod = _load_clipper()
    subtitles_applied = req.subtitles and mod.ffmpeg_has_subtitles()
    try:
        written = mod.merge_clips(path, spans, out_path,
                                  vertical=req.vertical, subtitles=req.subtitles)
    except Exception as exc:  # ffmpeg failure / bad path → 500 with the reason
        detail = getattr(exc, "stderr", None) or str(exc)
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        raise HTTPException(status_code=500, detail=f"Merge failed: {detail}")

    return {
        "source": os.path.basename(path),
        "output_dir": out_dir,
        "path": written,
        "clip_count": len(spans),
        "vertical": req.vertical,
        # True only if captions were actually burned (requested AND ffmpeg has libass).
        "subtitles_applied": subtitles_applied,
        "subtitles_requested": req.subtitles,
    }
