"""
Write SubtitleCue objects to a .ass file with karaoke-style per-word
highlighting, using the pysubs2 library.

Karaoke timing uses ASS \\k override tags, where each tag's value is the
highlight duration for that word in centiseconds (1/100s), computed
directly from each word's (end - start) in transcript_words.json.

IMPORTANT CAVEAT: karaoke timing is only as good as Whisper's word-level
timestamp accuracy. Static cues can absorb small per-word timing errors
invisibly; karaoke makes every word boundary visible on screen, in sync
with audio. If word timing looks tight/jittery in testing, that's a
transcription-accuracy limitation, not a bug in this writer.
"""

from typing import List

import pysubs2

from .cue_builder import SubtitleCue


def _parse_ass_hex_color(hex_str: str) -> pysubs2.Color:
    """
    Parse an ASS color string like '&H00FFFFFF' into a pysubs2.Color.

    ASS color format is &HAABBGGRR& (alpha-blue-green-red, NOT standard
    RGB order). Parsing manually here rather than relying on an unverified
    pysubs2 helper method — this format is fixed and simple enough that
    hand-parsing is more trustworthy than guessing a library API surface.
    """
    s = hex_str.replace("&H", "").replace("&", "")
    s = s.zfill(8)  # ensure AABBGGRR, pad if alpha omitted
    aa = int(s[0:2], 16)
    bb = int(s[2:4], 16)
    gg = int(s[4:6], 16)
    rr = int(s[6:8], 16)
    return pysubs2.Color(r=rr, g=gg, b=bb, a=aa)


def write_ass(
    cues: List[SubtitleCue],
    output_path: str,
    font_name: str = "Arial",
    font_size: int = 24,
    base_color: str = "&H00FFFFFF",       # white, unhighlighted words
    highlight_color: str = "&H0000D7FF",  # gold/yellow-ish highlight — verify visually, BGR order
    outline_color: str = "&H00000000",    # black outline
) -> str:
    """
    Render cues as karaoke-tagged ASS dialogue lines.

    Each cue becomes one Dialogue event. Within that event, every word gets
    a \\k<centiseconds> tag setting how long it's highlighted before the
    highlight advances to the next word.
    """
    subs = pysubs2.SSAFile()

    style = pysubs2.SSAStyle()
    style.fontname = font_name
    style.fontsize = font_size
    style.primarycolor = _parse_ass_hex_color(base_color)
    style.secondarycolor = _parse_ass_hex_color(highlight_color)  # \k uses secondary→primary transition
    style.outlinecolor = _parse_ass_hex_color(outline_color)
    style.outline = 2
    style.shadow = 0
    style.alignment = pysubs2.Alignment.BOTTOM_CENTER
    subs.styles["Karaoke"] = style

    for cue in cues:
        if not cue.words:
            continue

        karaoke_text = _build_karaoke_text(cue.words)

        event = pysubs2.SSAEvent(
            start=int(cue.start * 1000),
            end=int(cue.end * 1000),
            text=karaoke_text,
            style="Karaoke",
        )
        subs.append(event)

    subs.save(output_path)
    print(f"📄 Karaoke subtitles: {output_path}")
    return output_path


def _build_karaoke_text(words: List[dict]) -> str:
    parts = []
    for w in words:
        duration_cs = max(1, round((w["end"] - w["start"]) * 100))
        word_text = w["word"].strip()
        parts.append(f"{{\\k{duration_cs}}}{word_text} ")
    return "".join(parts).strip()