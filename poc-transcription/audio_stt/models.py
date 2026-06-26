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
    """A transcribed word with global timestamps and model confidence."""
    word: str
    start: float
    end: float
    probability: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "word": self.word.strip(),
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "probability": round(self.probability, 4),
        }