"""
Whisper Transcriber
====================
Runs MLX Whisper in single-pass full-file mode, sanitizes hallucination
artifacts, and produces transcript_segments.json / transcript_words.json.

NOTE: No manual chunking. mlx_whisper.transcribe() internally slides its
own 30s encoder window across the full waveform and conditions on previous
text by default — this is what gives coherent long-form segment timestamps.
Pre-slicing audio ourselves breaks that continuity, since each manually-cut
chunk starts the decoder cold with no context from what came before it.
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import WhisperConfig
from .audio_utils import load_audio
from .models import TranscriptSegment, TranscriptWord
from .sanitize import TranscriptSanitizer


class WhisperTranscriber:
    """
    Transcribes a preprocessed WAV file into word- and segment-level JSON
    in a single pass, with a sanitization step to strip decoder
    hallucination artifacts before anything is written to disk.

    Example:
        >>> transcriber = WhisperTranscriber(audio_path="input/hot-ones_preprocessed.wav")
        >>> transcriber.load_model()
        >>> segments, words = transcriber.run()
        >>> transcriber.save(segments, words, output_dir="output", base_name="hot-ones")
    """

    def __init__(
        self,
        audio_path: str,
        config: Optional[WhisperConfig] = None,
    ):
        self.audio_path = audio_path
        self.config = config or WhisperConfig()
        self._model_loaded = False
        self.sanitizer = TranscriptSanitizer(
            min_duration=self.config.sanitize_min_duration,
            max_segments_per_second=self.config.sanitize_max_segments_per_second,
            duplicate_timestamp_run_threshold=self.config.sanitize_duplicate_timestamp_threshold,
        )

    def load_model(self) -> None:
        """Load the MLX Whisper model once, before transcribing."""
        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder

        print(f"⏳ Loading model: {self.config.model}")
        load_start = time.perf_counter()
        ModelHolder.get_model(self.config.model, mx.float16)
        self._model_loaded = True
        print(f"✅ Model loaded in {time.perf_counter() - load_start:.2f}s")

    def run(self) -> "Tuple[List[TranscriptSegment], List[TranscriptWord]]":
        """Transcribe the full audio file in one pass, then sanitize the
        result to remove hallucination artifacts."""
        if not self._model_loaded:
            self.load_model()

        import mlx_whisper

        waveform, sample_rate = load_audio(self.audio_path, self.config.sample_rate)
        print(f"🔊 Audio duration: {len(waveform) / sample_rate:.1f}s")
        print("🎙️  Running single-pass transcription (this may take a while for long files)...")

        result = mlx_whisper.transcribe(
            waveform.astype("float32"),
            path_or_hf_repo=self.config.model,
            word_timestamps=True,
            language=self.config.language,
            verbose=False,
        )

        raw_segments: List[Dict] = []
        words_by_segment: Dict[int, List[TranscriptWord]] = {}

        for seg in result.get("segments", []):
            seg_id = seg["id"]  # reuse mlx_whisper's own segment ids directly
            raw_segments.append({
                "id": seg_id,
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
            })

            seg_words = []
            for w in seg.get("words", []):
                seg_words.append(TranscriptWord(
                    word=w["word"],
                    start=w["start"],
                    end=w["end"],
                    probability=w["probability"],
                    segment_id=seg_id,
                ))
            words_by_segment[seg_id] = seg_words

        print(f"📋 {len(raw_segments)} raw segments from Whisper")

        clean_dicts, dropped = self.sanitizer.run(raw_segments)

        if dropped:
            print(f"⚠️  Sanitizer dropped {len(dropped)} segment(s):")
            for d in dropped[:10]:
                preview = d["text"][:40] if d["text"] else "(empty)"
                print(f"   [{d['_reason']}] id={d['id']} start={d['start']:.2f} text={preview!r}")
            if len(dropped) > 10:
                print(f"   ... and {len(dropped) - 10} more")

        dropped_ids = {d["id"] for d in dropped}

        segments: List[TranscriptSegment] = [
            TranscriptSegment(id=d["id"], start=d["start"], end=d["end"], text=d["text"])
            for d in clean_dicts
        ]

        words: List[TranscriptWord] = []
        for seg_id, seg_words in words_by_segment.items():
            if seg_id in dropped_ids:
                continue
            words.extend(seg_words)

        return segments, words

    @staticmethod
    def save(
        segments: List[TranscriptSegment],
        words: List[TranscriptWord],
        output_dir: str,
        base_name: str,
    ) -> Dict[str, str]:
        """Save segments and words as flat-list JSON, matching the target format."""
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        segments_path = out_dir / f"{base_name}_transcript_segments.json"
        words_path = out_dir / f"{base_name}_transcript_words.json"

        with open(segments_path, "w", encoding="utf-8") as f:
            json.dump([s.to_dict() for s in segments], f, indent=2, ensure_ascii=False)
            f.write("\n")

        with open(words_path, "w", encoding="utf-8") as f:
            json.dump([w.to_dict() for w in words], f, indent=2, ensure_ascii=False)
            f.write("\n")

        print(f"📄 Segments: {segments_path}")
        print(f"📄 Words:    {words_path}")

        return {"segments": str(segments_path), "words": str(words_path)}