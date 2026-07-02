#!/usr/bin/env python3
"""
cut_viral_clips.py
==================
Memotong video asli menjadi klip-klip pendek viral (Combined, Relevancy, Virality)
secara akurat (re-encoding) dan menggabungkannya dengan audio bersih hasil preprocessing.

Penggunaan:
    python cut_viral_clips.py <path_video_asli> <path_json_rankings> [clean_audio_path] [output_dir]
"""

import os
import sys
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

def cut_category(video_path: str, clean_audio_path: str, clips_data: list, category_name: str, base_output_dir: str):
    """
    Memotong adegan untuk kategori tertentu (combined/relevancy/virality)
    """
    category_dir = os.path.join(base_output_dir, category_name)
    os.makedirs(category_dir, exist_ok=True)
    
    # Cek apakah audio bersih tersedia
    use_clean_audio = clean_audio_path and os.path.exists(clean_audio_path)
    if use_clean_audio:
        print(f"🔊 Menggunakan audio bersih (AGC+Denoise): {os.path.basename(clean_audio_path)}")
    else:
        print("⚠️ Berkas audio bersih tidak ditemukan/tidak diinput. Menggunakan audio asli bawaan video.")
        
    print(f"🎬 Memotong {len(clips_data)} adegan untuk kategori: '{category_name.upper()}'")
    
    for idx, clip in enumerate(clips_data, 1):
        start = clip["start"]
        end = clip["end"]
        duration = round(end - start, 2)
        
        # Bersihkan teks preview untuk penamaan file yang aman secara sistem operasi
        clean_text = "".join([c if c.isalnum() or c in " _-" else "" for c in clip["text"][:30]]).strip()
        clean_text = clean_text.replace(" ", "_")
        
        output_filename = f"clip_{idx:02d}_{start:.1f}s_{clean_text}.mp4"
        output_path = os.path.join(category_dir, output_filename)
        
        print(f"   ➜ [{idx}/{len(clips_data)}] {start:.1f}s ➜ {end:.1f}s ({duration:.1f}s) 💾 {output_filename}")
        
        if use_clean_audio:
            # Menggabungkan Trek Video (dari input 0) dan Trek Audio Bersih (dari input 1)
            # Menggunakan re-encoding agar keyframe potongan presisi dan tidak lag/hitam di awal video
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start), "-i", video_path,
                "-ss", str(start), "-i", clean_audio_path,
                "-t", str(duration),
                "-map", "0:v",  # Ambil visual dari video asli
                "-map", "1:a",  # Ambil audio dari WAV bersih
                "-c:v", "libx264",
                "-c:a", "aac",
                output_path
            ]
        else:
            # Fallback menggunakan audio asli bawaan video
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start), "-i", video_path,
                "-t", str(duration),
                "-c:v", "libx264",
                "-c:a", "aac",
                output_path
            ]
            
        try:
            # Jalankan pemotongan secara silent, abaikan output verbose FFmpeg kecuali error
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            print(f"   ❌ Gagal memotong klip {idx}: {e}")

def cut_all_rankings(video_path: str, json_path: str, clean_audio_path: str = None, output_dir: str = "output_clips"):
    """
    Muat berkas JSON dan proses pemotongan untuk ketiga kategori peringkat.
    """
    if not os.path.exists(video_path):
        print(f"❌ File video asli tidak ditemukan di: {video_path}")
        sys.exit(1)
    if not os.path.exists(json_path):
        print(f"❌ File JSON rankings tidak ditemukan di: {json_path}")
        sys.exit(1)
        
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    rankings = data.get("rankings", {})
    categories = {
        "combined": rankings.get("by_combined", []),
        "relevancy": rankings.get("by_relevancy", []),
        "virality": rankings.get("by_virality", [])
    }
    
    print("═" * 60)
    print(" ✂️  STARTING ACCURATE MULTI-CATEGORY VIDEO CUTTER")
    print("═" * 60)
    print(f" Video Asli   : {video_path}")
    print(f" Rankings JSON: {json_path}")
    print(f" Folder Output: {os.path.abspath(output_dir)}")
    print("═" * 60)
    
    for category_name, clips in categories.items():
        if clips:
            print(f"\n📁 Kategori: {category_name.upper()}")
            cut_category(video_path, clean_audio_path, clips, category_name, output_dir)
        else:
            print(f"\nℹ️ Kategori: {category_name.upper()} kosong di JSON. Dilewati.")
            
    print("\n🎉 Semua proses pemotongan klip selesai dengan sukses!")

_SUBS_CACHE = {}


def ffmpeg_has_subtitles() -> bool:
    """Whether this ffmpeg build has the libass ``subtitles`` filter (needed to
    burn in the karaoke captions). Homebrew builds without libass lack it, in
    which case we render the vertical video *without* subtitles rather than fail."""
    if "ok" not in _SUBS_CACHE:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                                 capture_output=True, text=True, check=False).stdout
            _SUBS_CACHE["ok"] = any(
                len(t := line.split()) >= 2 and t[1] == "subtitles"
                for line in out.splitlines())
        except Exception:
            _SUBS_CACHE["ok"] = False
    return _SUBS_CACHE["ok"]


def _safe_name(text: str) -> str:
    """Filesystem-safe clip name from the first few words of the line."""
    clean = "".join(c if c.isalnum() or c in " _-" else "" for c in (text or "")[:30]).strip()
    return clean.replace(" ", "_") or "clip"


def _render(video_path: str, clean_audio_path, start: float, duration: float,
            output_path: str, vertical: bool, ass_path=None) -> None:
    """Re-encode one span. Vertical → 1080×1920 portrait with a blurred fill
    background; ass_path (if given) burns the karaoke subtitles on top."""
    use_clean = bool(clean_audio_path) and os.path.exists(clean_audio_path)

    cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", video_path]
    if use_clean:
        cmd += ["-ss", str(start), "-i", clean_audio_path]
    cmd += ["-t", str(duration)]

    if vertical:
        # Blurred, stretched copy as the 9:16 background; the original scaled to
        # width sits centred on top; optional subtitles burned over the canvas.
        graph = ("[0:v]scale=1080:1920,boxblur=20:10[bg];"
                 "[0:v]scale=1080:-1[fg];"
                 "[bg][fg]overlay=(W-w)/2:(H-h)/2")
        if ass_path:
            esc = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            graph += f",subtitles='{esc}'"
        graph += ",scale=1080:1920[outv]"
        cmd += ["-filter_complex", graph, "-map", "[outv]",
                "-map", "1:a" if use_clean else "0:a",
                "-c:v", "libx264", "-c:a", "aac", "-s", "1080x1920", output_path]
    elif ass_path:
        # Landscape, but still burn the karaoke captions over the original frame.
        esc = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        cmd += ["-filter_complex", f"[0:v]subtitles='{esc}'[outv]", "-map", "[outv]",
                "-map", "1:a" if use_clean else "0:a",
                "-c:v", "libx264", "-c:a", "aac", output_path]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"] if use_clean else []
        cmd += ["-c:v", "libx264", "-c:a", "aac", output_path]

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def cut_clips(video_path: str, clips: list, output_dir: str,
              clean_audio_path: str = None,
              vertical: bool = True, subtitles: bool = True) -> list:
    """Cut each ``{start, end, text[, words]}`` clip out of *video_path* into *output_dir*.

    Library entry point used by the tldw backend's ``POST /clip`` endpoint. Unlike
    :func:`cut_all_rankings` (which reads a rankings JSON and writes per-category
    subfolders), this cuts a single flat list and *returns the written paths* so
    the caller can report them / reveal them in Finder. Each clip is re-encoded
    (libx264/aac) so the cut is frame-accurate at the start, not keyframe-aligned.

    Parameters:
        video_path:       Absolute path to the source video.
        clips:            Dicts with ``start`` / ``end`` (s) and ``text``; optional
                          ``words`` (``[{word,start,end}]``, source timeline) drive
                          the karaoke subtitles.
        output_dir:       Directory to write the ``.mp4`` files into (created if needed).
        clean_audio_path: Optional preprocessed WAV to mux in place of the source audio.
        vertical:         Render 1080×1920 portrait (blurred fill background) for Shorts.
        subtitles:        Burn word-by-word karaoke captions — only if this ffmpeg
                          has the libass ``subtitles`` filter (see
                          :func:`ffmpeg_has_subtitles`); silently skipped otherwise.

    Returns:
        List of absolute paths to the clips that were written, in input order.

    Raises:
        FileNotFoundError:      if *video_path* does not exist.
        subprocess.CalledProcessError: if an ffmpeg cut fails.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Source video not found: {video_path}")

    os.makedirs(output_dir, exist_ok=True)
    burn_subs = bool(subtitles) and ffmpeg_has_subtitles()
    if burn_subs:
        import subtitle_generator  # sibling module; POC dir is on sys.path

    written = []
    with tempfile.TemporaryDirectory(prefix="tldw_ass_") as ass_dir:
        for idx, clip in enumerate(clips, 1):
            start = float(clip["start"])
            end = float(clip["end"])
            duration = round(end - start, 2)
            if duration <= 0:
                continue

            output_path = os.path.join(
                output_dir, f"clip_{idx:02d}_{start:.1f}s_{_safe_name(clip.get('text', ''))}.mp4")

            ass_path = None
            if burn_subs:
                ass_path = os.path.join(ass_dir, f"clip_{idx:02d}.ass")
                sentences_data = [{"start": start, "end": end,
                                   "text": clip.get("text", ""),
                                   "words": clip.get("words") or []}]
                # emoji="" → no Llama; generate_ass_file falls back to even word
                # timing when a clip carries no word-level timestamps. `vertical`
                # sizes/positions the caption for the actual output frame.
                subtitle_generator.generate_ass_file(start, end, sentences_data, "", ass_path,
                                                     vertical=vertical)

            _render(video_path, clean_audio_path, start, duration,
                    output_path, vertical=vertical, ass_path=ass_path)
            written.append(os.path.abspath(output_path))

    return written


def _concat(paths: list, output_path: str) -> None:
    """Concatenate uniformly-encoded segments into one file via the concat demuxer.

    All segments come from :func:`cut_clips`, so they share codec/size/timebase
    (libx264 / aac / 1080×1920) and can be stream-copied without re-encoding."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        list_path = f.name
        for p in paths:
            # concat demuxer: single-quote paths, escaping any embedded quotes.
            safe = os.path.abspath(p).replace("'", "'\\''")
            f.write(f"file '{safe}'\n")
    try:
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
               "-c", "copy", output_path]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    finally:
        os.unlink(list_path)


def merge_clips(video_path: str, clips: list, output_path: str,
                clean_audio_path: str = None,
                vertical: bool = True, subtitles: bool = True) -> str:
    """Render each ``{start, end, text[, words]}`` span as a portrait+captioned
    segment (same pipeline as :func:`cut_clips`), then concatenate the segments
    into a single Short at *output_path*.

    Returns the absolute path to the written merged .mp4.

    Raises:
        FileNotFoundError: if *video_path* does not exist.
        ValueError:        if no span produced a segment.
        subprocess.CalledProcessError: if an ffmpeg render/concat fails.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Source video not found: {video_path}")

    out_abs = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(out_abs) or ".", exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="tldw_merge_") as seg_dir:
        segments = cut_clips(video_path, clips, seg_dir,
                             clean_audio_path=clean_audio_path,
                             vertical=vertical, subtitles=subtitles)
        if not segments:
            raise ValueError("No valid clip spans to merge.")
        if len(segments) == 1:
            shutil.copyfile(segments[0], out_abs)
        else:
            _concat(segments, out_abs)
    return out_abs


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Penggunaan: python cut_viral_clips.py <video_path> <json_path> [clean_audio_path] [output_dir]")
        sys.exit(1)
        
    v_path = sys.argv[1]
    j_path = sys.argv[2]
    a_path = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3].lower() != "none" else None
    out_dir = sys.argv[4] if len(sys.argv) > 4 else "output_clips"
    
    cut_all_rankings(v_path, j_path, a_path, out_dir)
