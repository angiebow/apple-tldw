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
import subprocess
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

def cut_clips(video_path: str, clips: list, output_dir: str,
              clean_audio_path: str = None) -> list:
    """Cut each ``{start, end, text}`` clip out of *video_path* into *output_dir*.

    Library entry point used by the tldw backend's ``POST /clip`` endpoint. Unlike
    :func:`cut_all_rankings` (which reads a rankings JSON and writes per-category
    subfolders), this cuts a single flat list and *returns the written paths* so
    the caller can report them / reveal them in Finder. Each clip is re-encoded
    (libx264/aac) so the cut is frame-accurate at the start, not keyframe-aligned.

    Parameters:
        video_path:       Absolute path to the source video.
        clips:            List of dicts with ``start`` / ``end`` (seconds) and ``text``.
        output_dir:       Directory to write the ``.mp4`` files into (created if needed).
        clean_audio_path: Optional preprocessed WAV to mux in place of the source
                          audio; falls back to the video's own audio when absent.

    Returns:
        List of absolute paths to the clips that were written, in input order.

    Raises:
        FileNotFoundError:      if *video_path* does not exist.
        subprocess.CalledProcessError: if an ffmpeg cut fails.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Source video not found: {video_path}")

    os.makedirs(output_dir, exist_ok=True)
    use_clean_audio = bool(clean_audio_path) and os.path.exists(clean_audio_path)

    written = []
    for idx, clip in enumerate(clips, 1):
        start = float(clip["start"])
        end = float(clip["end"])
        duration = round(end - start, 2)
        if duration <= 0:
            continue

        # Filesystem-safe name from the first few words of the line.
        clean_text = "".join(c if c.isalnum() or c in " _-" else "" for c in clip.get("text", "")[:30]).strip()
        clean_text = clean_text.replace(" ", "_") or "clip"
        output_path = os.path.join(output_dir, f"clip_{idx:02d}_{start:.1f}s_{clean_text}.mp4")

        if use_clean_audio:
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start), "-i", video_path,
                "-ss", str(start), "-i", clean_audio_path,
                "-t", str(duration),
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264", "-c:a", "aac",
                output_path,
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start), "-i", video_path,
                "-t", str(duration),
                "-c:v", "libx264", "-c:a", "aac",
                output_path,
            ]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        written.append(os.path.abspath(output_path))

    return written


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Penggunaan: python cut_viral_clips.py <video_path> <json_path> [clean_audio_path] [output_dir]")
        sys.exit(1)
        
    v_path = sys.argv[1]
    j_path = sys.argv[2]
    a_path = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3].lower() != "none" else None
    out_dir = sys.argv[4] if len(sys.argv) > 4 else "output_clips"
    
    cut_all_rankings(v_path, j_path, a_path, out_dir)
