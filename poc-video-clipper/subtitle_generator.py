#!/usr/bin/env python3
"""
subtitle_generator.py
=====================
Modul pembantu untuk menghasilkan file subtitle ASS karaoke per kata (word-by-word)
dengan emoji kontekstual (via Llama 3.2 3B) dan merender video potret (9:16)
dengan latar belakang video blur adaptif menggunakan FFmpeg.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Any

def generate_emoji_for_text(text: str, model: Any, tokenizer: Any) -> str:
    """
    Meminta Llama 3.2 3B untuk memberikan 1 emoji kontekstual paling relevan.
    """
    # Lazy import: only the (optional) emoji path needs Llama/mlx_lm. The ASS +
    # vertical-render functions below have no LLM dependency, so importing this
    # module for those never pulls in mlx_lm.
    from mlx_lm import generate

    prompt = (
        "You are an emoji generator. Analyze the text and output exactly ONE or TWO emojis "
        "that best match the theme, mood, or keyword of the sentence. "
        "Output ONLY the emoji itself. No text, no explanation, no punctuation.\n\n"
        f"Text: \"{text}\"\n"
        "Emoji:"
    )
    messages = [{"role": "user", "content": prompt}]
    formatted_prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    emoji = generate(model, tokenizer, prompt=formatted_prompt, max_tokens=10, verbose=False).strip()
    
    # Bersihkan jika ada output teks tambahan (hanya ambil emoji Unicode)
    emoji_match = re.findall(r"[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff]", emoji)
    return "".join(emoji_match) if emoji_match else "🐣"

def generate_ass_file(
    clip_start: float,
    clip_end: float,
    sentences_data: list,
    emoji: str,
    output_ass_path: str
):
    """
    Membuat file subtitle ASS dengan highlight box merah per kata (word-by-word)
    serta emoji di atasnya. Membagi kata menjadi grup-grup kecil berisi maksimal 3 kata
    agar rapi di layar vertikal 9:16.
    """
    # Kumpulkan semua kata
    all_words = []
    for seg in sentences_data:
        seg_words = seg.get("words", [])
        if not seg_words:
            # Fallback jika tidak ada data per kata
            words_list = seg["text"].split()
            duration = seg["end"] - seg["start"]
            word_dur = duration / max(len(words_list), 1)
            for i, w in enumerate(words_list):
                all_words.append({
                    "word": w,
                    "start": seg["start"] + (i * word_dur),
                    "end": seg["start"] + ((i + 1) * word_dur)
                })
        else:
            all_words.extend(seg_words)
            
    # Bersihkan & buat waktu relatif terhadap awal klip
    for w in all_words:
        w["clip_start"] = max(0.0, w["start"] - clip_start)
        w["clip_end"] = max(0.0, w["end"] - clip_start)
        
    def to_ass_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h:01d}:{m:02d}:{s:05.2f}"

    # PEMBAGIAN GRUP: Batasi maksimal 3 kata per baris agar rapi di layar vertikal
    words_per_line = 3
    groups = [all_words[i:i+words_per_line] for i in range(0, len(all_words), words_per_line)]
    
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "ScaledBorderAndShadow: yes\n\n"
        
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # Style Aktif: Box Merah (&H0000FF), Teks Putih (&HFFFFFF), Alignment tengah-bawah (2), Margin vertikal 350
        "Style: Active,Arial,85,&HFFFFFF,&HFFFFFF,&H0000FF,&H0000FF,1,0,0,0,100,100,0,0,3,0,0,2,10,10,350,1\n"
        # Style Inaktif: Box Hitam Transparan (&H80000000), Teks Abu-abu (&HCCCCCC), Alignment tengah-bawah (2), Margin vertikal 350
        "Style: Inactive,Arial,85,&HCCCCCC,&HCCCCCC,&H80000000,&H80000000,1,0,0,0,100,100,0,0,3,0,0,2,10,10,350,1\n\n"
        
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    
    events = []
    for group in groups:
        group_start = group[0]["clip_start"]
        group_end = group[-1]["clip_end"]
        
        for active_idx, active_word in enumerate(group):
            w_start = active_word["clip_start"]
            w_end = active_word["clip_end"]
            
            # Rentangkan start/end agar menutupi seluruh durasi grup (mencegah kedipan)
            if active_idx == 0:
                w_start = group_start
            if active_idx == len(group) - 1:
                w_end = group_end
                
            t_start = to_ass_time(w_start)
            t_end = to_ass_time(w_end)
            
            line_parts = []
            for idx, w in enumerate(group):
                word_clean = w["word"].strip().upper()  # Konversi ke huruf kapital tebal
                if idx == active_idx:
                    line_parts.append(f"{{\\rActive}}{word_clean}")
                else:
                    line_parts.append(f"{{\\rInactive}}{word_clean}")
                    
            # Emoji sits on its own line above the words; skip it when there's no
            # emoji (we don't run the optional Llama emoji step in the app path).
            emoji_prefix = f"{{\\rActive}}{emoji}\\N" if emoji else ""
            dialogue_text = emoji_prefix + " ".join(line_parts)
            events.append(f"Dialogue: 0,{t_start},{t_end},Inactive,,0,0,0,,{dialogue_text}")
            
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n".join(events))
        f.write("\n")

def render_portrait_video_with_subs(
    video_path: str,
    clean_audio_path: str,
    start_time: float,
    duration: float,
    ass_path: str,
    output_path: str
) -> bool:
    """
    Merender video potret (9:16) dengan latar belakang video blur adaptif (boxblur)
    dan menyatukan subtitle ASS karaoke menggunakan FFmpeg.
    """
    use_clean_audio = clean_audio_path and os.path.exists(clean_audio_path)
    
    # Filter Graph FFmpeg:
    # 1. Visual video asli direntangkan paksa ke 1080x1920 dan diblur tebal sebagai background.
    # 2. Visual video asli diskalakan pas lebar 1080px di tengah sebagai foreground (overlay).
    # 3. Keduanya digabung (overlay) lalu ditumpuk subtitle ASS di atas kanvas 9:16 tersebut.
    # 4. Kunci skala output final filter video ke 1080x1920.
    filter_complex = (
        "[0:v]scale=1080:1920,boxblur=20:10[bg];"
        "[0:v]scale=1080:-1[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        f"subtitles='{ass_path}',scale=1080:1920[outv]"
    )
    
    if use_clean_audio:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_time), "-i", video_path,
            "-ss", str(start_time), "-i", clean_audio_path,
            "-t", str(duration),
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "1:a",  # Gunakan audio bersih
            "-c:v", "libx264",
            "-c:a", "aac",
            "-s", "1080x1920",  # Paksa output video final berukuran vertikal 1080x1920
            output_path
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_time), "-i", video_path,
            "-t", str(duration),
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "0:a",  # Gunakan audio video asli
            "-c:v", "libx264",
            "-c:a", "aac",
            "-s", "1080x1920",  # Paksa output video final berukuran vertikal 1080x1920
            output_path
        ]
        
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError as e:
        print(f"       ❌ FFmpeg render error: {e}")
        return False
