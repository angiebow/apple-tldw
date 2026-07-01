"""
Transcription Data Models
==========================
Matches the existing transcript_segments.json / transcript_words.json format
used in poc-audio-extraction/output — flat list, no wrapper object.
"""

from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class TranscriptSegment:
    """A transcribed speech segment with global timestamps."""
    id: int
    start: float
    end: float
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text.strip(),
        }


@dataclass
class TranscriptWord:
    """
    A transcribed word with global timestamps and model confidence.

    segment_id links this word back to the TranscriptSegment it came from,
    so the sanitizer can drop a segment's words when the segment itself is
    flagged as a hallucination artifact. Not written to transcript_words.json
    (see to_dict) — downstream consumers (subtitles, NLP) don't need it.
    """
    word: str
    start: float
    end: float
    probability: float
    segment_id: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "word": self.word.strip(),
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "probability": round(self.probability, 4),
        }