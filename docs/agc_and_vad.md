# AGC & VAD Workflows

How ViReel levels audio (**AGC**) and finds speech (**VAD**) inside the audio
preprocessing stage. Both live in `poc-audio-extraction/audio_preprocessor/` and
run before Whisper transcription.

## Where they sit in the pipeline

Audio is cleaned in a fixed order so each stage feeds the next a better signal:

```
Raw video / audio
      │
      ▼
1. Extract  ── FFmpeg → 16 kHz mono WAV (pcm_s16le)
      │
      ▼
2. Denoise  ── DeepFilterNet (adaptive: none / mild / strong by SNR)
      │
      ▼
3. AGC      ── FFmpeg dynaudnorm — level internal volume dynamics
      │
      ▼
4. Loudness ── pyloudnorm → conform to −23 LUFS (EBU R128)
      │
      ▼
5. VAD      ── Silero VAD → speech-segment timestamps
      │
      ▼
   Whisper STT  (+ blooper detection inverts the speech spans)
```

**Why this order?** Denoise first so AGC doesn't amplify background hiss →
AGC to even out quiet vs. loud passages → loudness normalization to hit an
exact broadcast target → VAD last, so Silero sees a clean, consistently-loud
signal and detects speech more reliably.

---

## AGC — Automatic Gain Control

**Goal:** even out volume so a whispered aside and a shouted point sit at a
usable level, without clipping. It's dynamic range compression, not a single
volume bump.

**Implementation:** `agc.py` shells out to FFmpeg's `dynaudnorm` (Dynamic Audio
Normalizer) filter. Unlike a one-shot gain change, `dynaudnorm` slides a
Gaussian-weighted window across the track and re-computes gain frame by frame.

### Workflow

```
Input WAV (denoised)
      │
      ▼
Build filter string from AGCConfig:
   dynaudnorm=f=150 : g=15 : m=10.0 : p=0.95
      │        │        │       │        │
      │        │        │       │        └─ target_peak  → aim each frame at 0.95 peak
      │        │        │       └────────── max_gain     → never boost more than 10×
      │        │        └────────────────── filter_size  → 15-frame Gaussian smoothing window
      │        └─────────────────────────── frame_len_ms → analyze in 150 ms frames
      │
      ▼
Run: ffmpeg -y -i input.wav -af "<filter>" output.wav
      │
      ▼
Leveled WAV  →  handed to loudness normalization
```

### Behavior notes
- **Disabled path:** if `AGCConfig.enabled == False`, the stage just copies the
  file through untouched — the pipeline stays runnable.
- **Smoothing** (`filter_size=15`) prevents audible "pumping" by averaging gain
  across neighboring frames instead of reacting to every 150 ms frame.
- **`max_gain=10.0`** caps how hard silence-adjacent noise can be lifted, so a
  quiet gap isn't blown up into a roar.
- Failures raise `AGCError` with the FFmpeg command + stderr for debugging.

### Config (`AGCConfig`, defaults)
| Field | Default | Meaning |
|---|---|---|
| `enabled` | `True` | Toggle AGC on/off |
| `frame_len_ms` | `150` | Analysis frame length (ms) |
| `filter_size` | `15` | Gaussian smoothing window (frames) |
| `max_gain` | `10.0` | Maximum gain factor per frame |
| `target_peak` | `0.95` | Peak each frame is normalized toward |

---

## VAD — Voice Activity Detection

**Goal:** find *where someone is actually talking* and return those spans as
timestamps. Whisper uses them to focus transcription; blooper detection inverts
them to find dead air.

**Implementation:** `vad.py` wraps the official **Silero VAD** model, loaded
lazily from `torch.hub` (`snakers4/silero-vad`) on first use so importing the
module needs no network access.

### Workflow

```
Input WAV (denoised + leveled + −23 LUFS)
      │
      ▼
Lazy-load Silero VAD model (torch.hub, cached after first call)
      │
      ▼
Load WAV as 1-D float32 tensor  (multi-channel → mixed down to mono)
      │
      ▼
get_speech_timestamps(audio, model, …VADConfig):
   threshold=0.5              → speech-probability cutoff (lower = more sensitive)
   min_speech_duration_ms=250 → ignore blips shorter than 250 ms
   min_silence_duration_ms=500→ a gap ≥ 500 ms ends a speech segment
   window_size_samples=1024   → Silero inference window (16 kHz: 512/1024/1536)
   speech_pad_ms=30           → pad 30 ms around each segment so edges aren't clipped
      │
      ▼
Raw segments in SAMPLE indices  →  convert to SECONDS (÷ sample_rate), sort
      │
      ▼
VADResult:
   • segments[]            → {start, end} in seconds
   • total_speech_duration → sum of segment lengths
   • speech_ratio          → speech time ÷ total duration
      │
      ├─► Whisper STT  (transcribe guided by speech spans)
      └─► Blooper detection  (INVERT spans → non-speech = silence / dead air)
```

### Design decisions
- **Timestamps in seconds, not samples** — downstream stages stay agnostic to
  sample-rate changes.
- **Original timeline preserved** — segments reference the source file's clock,
  not chunk offsets, so words line up with the video.
- **No audio chunks written to disk** — VAD output is *pure metadata*, letting
  any STT backend consume the full audio with timestamps.
- **Mono mix-down** — multi-channel input is averaged to one channel before
  inference.

### Config (`VADConfig`, defaults)
| Field | Default | Meaning |
|---|---|---|
| `threshold` | `0.5` | Speech-probability cutoff (lower → more sensitive) |
| `min_speech_duration_ms` | `250` | Shortest span counted as speech |
| `max_speech_duration_s` | `3600.0` | Effectively unlimited segment length |
| `min_silence_duration_ms` | `500` | Silence gap that ends a segment |
| `window_size_samples` | `1024` | Silero inference window (512/1024/1536) |
| `speech_pad_ms` | `30` | Padding added around each detected segment |

---

## How VAD feeds blooper detection

The blooper detector doesn't run its own silence finder — it **reuses the VAD
speech spans and inverts them**. Everything *between* speech segments (plus the
merge/pad interval math) becomes a candidate "blooper" (silence, long pause,
dead air, dropped mic). There's no minimum-duration threshold, so every
non-speech gap is a candidate; the practical floor (~0.2 s) falls out of the
merge-gap and padding settings rather than a user-facing knob.
