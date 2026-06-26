"""
Whisper Transcriber
====================
Runs MLX Whisper over VAD-guided, OOM-safe chunks and produces
transcript_segments.json / transcript_words.json.
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from .config import WhisperConfig
from .audio_utils import load_audio, load_vad_segments, chunk_segments
from .models import TranscriptSegment, TranscriptWord


class WhisperTranscriber:
    """
    Transcribes a preprocessed WAV file into word- and segment-level JSON,
    guided by VAD metadata.

    Example:
        >>> transcriber = WhisperTranscriber(
        ...     audio_path="input/hot-ones_preprocessed.wav",
        ...     vad_path="input/hot-ones_vad_metadata.json",
        ... )
        >>> transcriber.load_model()
        >>> segments, words = transcriber.run()
        >>> transcriber.save(segments, words, output_dir="output", base_name="hot-ones")
    """

    def __init__(
        self,
        audio_path: str,
        vad_path: str,
        config: Optional[WhisperConfig] = None,
    ):
        self.audio_path = audio_path
        self.vad_path = vad_path
        self.config = config or WhisperConfig()
        self._model_loaded = False

    def load_model(self) -> None:
        """Load the MLX Whisper model once, before transcribing any chunks."""
        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder

        print(f"⏳ Loading model: {self.config.model}")
        load_start = time.perf_counter()
        ModelHolder.get_model(self.config.model, mx.float16)
        self._model_loaded = True
        print(f"✅ Model loaded in {time.perf_counter() - load_start:.2f}s")

    def run(self) -> "tuple[List[TranscriptSegment], List[TranscriptWord]]":
        """Transcribe the full audio file, chunked and VAD-guided."""
        if not self._model_loaded:
            self.load_model()

        import mlx_whisper

        waveform, sample_rate = load_audio(self.audio_path, self.config.sample_rate)
        vad_segments = load_vad_segments(self.vad_path)
        chunks = chunk_segments(
            waveform,
            sample_rate,
            vad_segments,
            max_chunk_duration=self.config.max_chunk_duration,
            max_merge_gap=self.config.max_merge_gap,
        )

        print(f"🔊 Audio duration: {len(waveform) / sample_rate:.1f}s")
        print(f"📋 VAD segments: {len(vad_segments)} → {len(chunks)} model chunks after merge/split")

        segments: List[TranscriptSegment] = []
        words: List[TranscriptWord] = []
        segment_id = 0

        for idx, chunk in enumerate(chunks):
            chunk_start = chunk["start"]
            print(f"  [{idx + 1}/{len(chunks)}] {chunk_start:.2f}s - {chunk['end']:.2f}s "
                  f"({chunk['end'] - chunk_start:.1f}s)…")

            result = mlx_whisper.transcribe(
                chunk["waveform"].astype("float32"),
                path_or_hf_repo=self.config.model,
                word_timestamps=True,
                language=self.config.language, #is this needed
                verbose=False,
            )

            for seg in result.get("segments", []):
                segments.append(TranscriptSegment(
                    id=segment_id,
                    start=chunk_start + seg["start"],
                    end=chunk_start + seg["end"],
                    text=seg["text"],
                ))
                segment_id += 1

                for w in seg.get("words", []):
                    words.append(TranscriptWord(
                        word=w["word"],
                        start=chunk_start + w["start"],
                        end=chunk_start + w["end"],
                        probability=w["probability"],
                    ))

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