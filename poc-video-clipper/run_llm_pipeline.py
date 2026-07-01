#!/usr/bin/env python3
"""
run_llm_pipeline.py
===================
Pipeline terpadu satu pintu (End-to-End) untuk memproses video podcast mentah:
1. Mengekstrak & membersihkan audio (Denoise + AGC + VAD).
2. Melakukan transkripsi Whisper STT dengan Word-Level Timestamps.
3. Menganalisis topik & memisah adegan secara tata bahasa (durasi >= 10 detik).
4. Menilai relevansi & viralitas adegan menggunakan Llama 3.2 3B.
5. Memotong video fisik ke format Portrait 9:16 dengan video blur adaptif,
   emoji kontekstual, dan subtitle ASS karaoke per kata (box merah) secara otomatis.
"""

import os
import sys
import json
import time
import re
import subprocess
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Tuple
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────── #
# Pengaman Jalur Folder Kerja & Impor Modul                                    #
# ─────────────────────────────────────────────────────────────────────────── #
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import torch
import numpy as np
import torchaudio
import whisper
from transformers import AutoTokenizer
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from mlx_lm import load, generate

from audio_preprocessor import AudioPreprocessor
from audio_preprocessor.config import PreprocessorConfig, FFmpegConfig, DenoiseConfig, VADConfig

# ─────────────────────────────────────────────────────────────────────────── #
# Struktur Data                                                               #
# ─────────────────────────────────────────────────────────────────────────── #

@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str
    words: List[Dict[str, Any]] = None

@dataclass
class Scene:
    start: float
    end: float
    duration: float
    text: str
    sentences: List[Segment]

# ─────────────────────────────────────────────────────────────────────────── #
# Fungsi Pembantu                                                             #
# ─────────────────────────────────────────────────────────────────────────── #

def split_scene_recursive(
    scene_segments: List[Segment], 
    embeddings_map: Dict[int, np.ndarray], 
    max_duration: float = 60.0
) -> List[List[Segment]]:
    """
    Recursively splits a scene if its duration exceeds the max_duration.
    """
    duration = scene_segments[-1].end - scene_segments[0].start
    if duration <= max_duration:
        return [scene_segments]
        
    if len(scene_segments) <= 1:
        return [scene_segments]
        
    lowest_sim = float("inf")
    split_idx = -1
    
    # 1. Cari titik potong tata bahasa
    grammatical_indices = []
    for i in range(len(scene_segments) - 1):
        seg_a = scene_segments[i]
        seg_b = scene_segments[i+1]
        
        text_a_stripped = seg_a.text.strip()
        ends_with_punctuation = text_a_stripped and text_a_stripped[-1] in [".", "?", "!"]
        
        text_b_stripped = seg_b.text.strip()
        next_is_capitalized = len(text_b_stripped) > 0 and text_b_stripped[0].isupper()
        
        if ends_with_punctuation and next_is_capitalized:
            grammatical_indices.append(i)
            
    # 2. Pilih similarity terkecil
    if grammatical_indices:
        for i in grammatical_indices:
            seg_a = scene_segments[i]
            seg_b = scene_segments[i+1]
            emb_a = embeddings_map[seg_a.id].reshape(1, -1)
            emb_b = embeddings_map[seg_b.id].reshape(1, -1)
            sim = cosine_similarity(emb_a, emb_b)[0][0]
            if sim < lowest_sim:
                lowest_sim = sim
                split_idx = i
    else:
        for i in range(len(scene_segments) - 1):
            seg_a = scene_segments[i]
            seg_b = scene_segments[i+1]
            emb_a = embeddings_map[seg_a.id].reshape(1, -1)
            emb_b = embeddings_map[seg_b.id].reshape(1, -1)
            sim = cosine_similarity(emb_a, emb_b)[0][0]
            if sim < lowest_sim:
                lowest_sim = sim
                split_idx = i
                
    if split_idx == -1:
        return [scene_segments]
        
    left_part = scene_segments[:split_idx + 1]
    right_part = scene_segments[split_idx + 1:]
    
    return (split_scene_recursive(left_part, embeddings_map, max_duration) + 
            split_scene_recursive(right_part, embeddings_map, max_duration))

def get_mlx_model_path(model_name: str) -> str:
    mapping = {
        "tiny": "mlx-community/whisper-tiny-mlx",
        "tiny.en": "mlx-community/whisper-tiny.en-mlx",
        "base": "mlx-community/whisper-base-mlx",
        "base.en": "mlx-community/whisper-base.en-mlx",
        "small": "mlx-community/whisper-small-mlx",
        "small.en": "mlx-community/whisper-small.en-mlx",
        "medium": "mlx-community/whisper-medium-mlx",
        "medium.en": "mlx-community/whisper-medium.en-mlx",
        "large": "mlx-community/whisper-large-v3-mlx",
        "turbo": "mlx-community/whisper-large-v3-turbo"
    }
    return mapping.get(model_name.lower(), f"mlx-community/whisper-{model_name}-mlx")

# ─────────────────────────────────────────────────────────────────────────── #
# Pipeline Eksekusi Utama                                                      #
# ─────────────────────────────────────────────────────────────────────────── #

def run_full_clipper_pipeline():
    print("═" * 60)
    print("  🎬  ONE-CLICK AUTOMATED VIDEO CLIPPER PIPELINE")
    print("═" * 60)
    
    # 1. Tanya file video mentah secara interaktif
    while True:
        raw_vid = input("  📁 Drag & drop berkas video asli ke sini (lalu tekan Enter): ").strip()
        raw_vid = raw_vid.strip("'\"")
        if not raw_vid:
            continue
        video_path = os.path.abspath(raw_vid)
        if os.path.exists(video_path):
            break
        print("  ❌ File video tidak ditemukan! Silakan coba kembali.")
        
    video_stem = Path(video_path).stem
    output_dir = os.path.join(SCRIPT_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)
    
    # Setup path internal dinamis
    clean_audio_path = os.path.join(output_dir, f"{video_stem}_preprocessed.wav")
    vad_metadata_path = os.path.join(output_dir, f"{video_stem}_vad_metadata.json")
    transcript_path = os.path.join(output_dir, f"{video_stem}_transcript_segments.json")
    
    # ── TAHAP I: PREPROCESSING AUDIO (Denoise, AGC, VAD) ──
    print("\n⏳ [1/5] Memulai pembersihan audio dan analisis VAD…")
    config = PreprocessorConfig(
        output_dir=output_dir,
        log_level="INFO",
        ffmpeg=FFmpegConfig(sample_rate=16000, channels=1, codec="pcm_s16le"),
        denoise=DenoiseConfig(snr_clean_threshold=20.0, snr_mild_threshold=10.0),
        vad=VADConfig(threshold=0.5, min_speech_duration_ms=250, min_silence_duration_ms=500)
    )
    preprocessor = AudioPreprocessor(config)
    preprocessor.process(video_path)
    
    # Logika Rename Otomatis dari file statis preprocessor ke format nama video dinamis
    static_audio = os.path.join(output_dir, "preprocessed.wav")
    static_vad = os.path.join(output_dir, "vad_metadata.json")
    static_report = os.path.join(output_dir, "processing_report.json")
    
    if os.path.exists(static_audio):
        if os.path.exists(clean_audio_path):
            os.remove(clean_audio_path)
        os.rename(static_audio, clean_audio_path)
        
    if os.path.exists(static_vad):
        if os.path.exists(vad_metadata_path):
            os.remove(vad_metadata_path)
        os.rename(static_vad, vad_metadata_path)
        
    if os.path.exists(static_report):
        dynamic_report = os.path.join(output_dir, f"{video_stem}_processing_report.json")
        if os.path.exists(dynamic_report):
            os.remove(dynamic_report)
        os.rename(static_report, dynamic_report)
        
    print("✅ Preprocessing Audio Selesai dan berkas berhasil di-rename!")
    
    # ── TAHAP II: TRANSKRIPSI WHISPER STT DENGAN WORD-LEVEL TIMESTAMPS ──
    print("\n⏳ [2/5] Memulai transkripsi suara dengan Whisper (Word-Level Timestamps)...")
    has_mlx = False
    try:
        import mlx.core as mx
        import mlx_whisper
        has_mlx = True
    except ImportError:
        pass
        
    backend = "mlx" if has_mlx else "openai"
    whisper_model_name = "medium"
    
    print(f"   ⚙️  Whisper Backend : {backend.upper()}")
    print(f"   ⚙️  Whisper Model   : {whisper_model_name}")
    
    # Load Whisper
    if backend == "mlx":
        mlx_model_path = get_mlx_model_path(whisper_model_name)
        from mlx_whisper.transcribe import ModelHolder
        model = ModelHolder.get_model(mlx_model_path, mx.float16)
    else:
        stt_device = "mps" if torch.backends.mps.is_available() else "cpu"
        model = whisper.load_model(whisper_model_name, device=stt_device)
        
    # Muat audio dan segmen suara VAD
    waveform, sample_rate = torchaudio.load(clean_audio_path)
    with open(vad_metadata_path, "r", encoding="utf-8") as f:
        vad_data = json.load(f)
    speech_segments = vad_data.get("speech_segments", [])
    
    print(f"   🎙️  Memproses {len(speech_segments)} segmen suara percakapan...")
    global_segments = []
    segment_id = 0
    
    for idx, seg in enumerate(speech_segments):
        start_time = seg["start"]
        end_time = seg["end"]
        
        start_sample = int(start_time * sample_rate)
        end_sample = int(end_time * sample_rate)
        segment_audio = waveform[0, start_sample:end_sample].numpy()
        
        if len(segment_audio) < 160:
            continue
            
        if backend == "mlx":
            res = mlx_whisper.transcribe(
                segment_audio.astype("float32"),
                path_or_hf_repo=mlx_model_path,
                word_timestamps=True,  # Nyalakan Word-Level Timestamps!
                verbose=False
            )
        else:
            res = model.transcribe(segment_audio, word_timestamps=True, fp16=False)
            
        for s in res.get("segments", []):
            # Hitung timestamps absolut per kata terhadap video asli
            words_data = []
            for w in s.get("words", []):
                words_data.append({
                    "word": w["word"],
                    "start": round(start_time + w["start"], 3),
                    "end": round(start_time + w["end"], 3)
                })
                
            global_segments.append({
                "id": segment_id,
                "start": round(start_time + s["start"], 3),
                "end": round(start_time + s["end"], 3),
                "text": s["text"].strip(),
                "words": words_data
            })
            segment_id += 1
            
    with open(transcript_path, "w", encoding="utf-8") as f:
        json.dump(global_segments, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"✅ Transkripsi Whisper Selesai! Disimpan ke: {transcript_path}")

    # ── TAHAP III: NLP & JURI LLAMA 3.2 ──
    print("\n⏳ [3/5] Memuat model NLP Llama 3.2 3B & MPNet...")
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    pegasus_model, pegasus_tokenizer = load("mlx-community/Llama-3.2-3B-Instruct-4bit")
    embedding_model = SentenceTransformer("sentence-transformers/all-mpnet-base-v2", device=device)
    
    # Parse transcript
    scenes = []
    segments = [
        Segment(
            id=s["id"], 
            start=s["start"], 
            end=s["end"], 
            text=s["text"],
            words=s.get("words", [])
        ) 
        for s in global_segments
    ]
    
    # 1. Chunking
    chunks = []
    current_chunk = []
    current_tokens = 0
    for seg in segments:
        seg_tokens = len(pegasus_tokenizer.encode(seg.text, add_special_tokens=False))
        if current_tokens + seg_tokens > 1024:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = [seg]
            current_tokens = seg_tokens
        else:
            current_chunk.append(seg)
            current_tokens += seg_tokens
    if current_chunk:
        chunks.append(current_chunk)
        
    # 2. Chunk Summaries
    chunk_summaries = []
    print("   📝 Merangkum potongan transkrip...")
    for chunk in chunks:
        chunk_text = " ".join([s.text for s in chunk])
        prompt = (
            "You are a professional editor. Meticulously summarize the following transcript chunk. "
            "Do NOT assume or guess the names of any speakers. "
            "Start the summary directly with the key points. Limit introduction phrases.\n\n"
            f"Transcript:\n{chunk_text}\n\nSummary:"
        )
        messages = [{"role": "user", "content": prompt}]
        formatted_prompt = pegasus_tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        summary = generate(pegasus_model, pegasus_tokenizer, prompt=formatted_prompt, max_tokens=150, verbose=False).strip()
        chunk_summaries.append(summary)
        
    # 3. Global Summary
    combined_chunk_summaries = " ".join(chunk_summaries)
    prompt = (
        "Synthesize a cohesive global summary based on these chunk summaries. "
        "Limit to exactly 2-3 sentences.\n\n"
        f"Summaries:\n{combined_chunk_summaries}\n\nGlobal Summary:"
    )
    messages = [{"role": "user", "content": prompt}]
    formatted_prompt = pegasus_tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    global_summary = generate(pegasus_model, pegasus_tokenizer, prompt=formatted_prompt, max_tokens=256, verbose=False).strip()
    print(f"   📝 Global Summary: \"{global_summary[:120]}...\"")
    
    # 4. Scene Segmentation (Grammatical Guard & >= 10s duration)
    print("   🎬 Segmentasi adegan cerdas...")
    sentences_text = [seg.text for seg in segments]
    embeddings = embedding_model.encode(sentences_text, show_progress_bar=False)
    embeddings_map = {seg.id: embeddings[i] for i, seg in enumerate(segments)}
    
    emb_a = embeddings[:-1]
    emb_b = embeddings[1:]
    a_norm = emb_a / np.linalg.norm(emb_a, axis=1, keepdims=True)
    b_norm = emb_b / np.linalg.norm(emb_b, axis=1, keepdims=True)
    similarities = np.sum(a_norm * b_norm, axis=1)
    
    mean_sim = np.mean(similarities)
    std_sim = np.std(similarities)
    threshold = mean_sim - 0.5 * std_sim
    
    scene_groups = []
    current_group = [segments[0]]
    for i in range(len(similarities)):
        seg_current = segments[i]
        seg_next = segments[i+1]
        text_stripped = seg_current.text.strip()
        ends_with_punctuation = text_stripped and text_stripped[-1] in [".", "?", "!"]
        next_text_stripped = seg_next.text.strip()
        next_is_capitalized = len(next_text_stripped) > 0 and next_text_stripped[0].isupper()
        
        if similarities[i] < threshold and ends_with_punctuation and next_is_capitalized:
            scene_groups.append(current_group)
            current_group = [seg_next]
        else:
            current_group.append(seg_next)
    if current_group:
        scene_groups.append(current_group)
        
    for group in scene_groups:
        split_groups = split_scene_recursive(group, embeddings_map, max_duration=60.0)
        for split_group in split_groups:
            start = split_group[0].start
            end = split_group[-1].end
            duration = end - start
            
            # Saring adegan di bawah 10 detik sejak awal
            if duration < 10.0:
                continue
                
            scene_text = " ".join([s.text for s in split_group])
            scenes.append(Scene(
                start=round(start, 2),
                end=round(end, 2),
                duration=round(duration, 2),
                text=scene_text,
                sentences=split_group
            ))
            
    print(f"   🎬 Terbentuk {len(scenes)} adegan valid (durasi 10s - 60s).")
    
    # 5. Relevancy & Virality Classification
    print("   ⚖️  Menilai relevansi & potensi viralitas adegan via Llama...")
    global_summary_emb = embedding_model.encode(global_summary, show_progress_bar=False).reshape(1, -1)
    
    for scene in scenes:
        # Relevancy
        sc_emb = embedding_model.encode(scene.text, show_progress_bar=False).reshape(1, -1)
        sim = cosine_similarity(sc_emb, global_summary_emb)[0][0]
        object.__setattr__(scene, "relevancy_score", round(float(np.clip(sim, 0.0, 1.0)), 4))
        
        # Llama Virality Rater
        prompt = (
            "You are an expert social media editor. Analyze the video segment and rate its viral potential "
            "from 1 to 10 (1 = extremely boring/routine, 10 = highly engaging hook/key quote/dramatic reveal).\n\n"
            f"Transcript: \"{scene.text}\"\n"
            "Rating (Respond ONLY with a single integer between 1 and 10):"
        )
        messages = [{"role": "user", "content": prompt}]
        formatted_prompt = pegasus_tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        res_text = generate(pegasus_model, pegasus_tokenizer, prompt=formatted_prompt, max_tokens=5, verbose=False).strip()
        
        score_match = re.search(r"\d+", res_text)
        try:
            rating = float(score_match.group(0)) if score_match else 5.0
            rating = np.clip(rating, 1.0, 10.0)
            score = rating / 10.0
        except ValueError:
            score = 0.5
            
        object.__setattr__(scene, "viral_label", 1 if score >= 0.5 else 0)
        object.__setattr__(scene, "viral_score", round(score, 4))
        
    print("✅ Penilaian Juri Llama Selesai!")
    
    # ── TAHAP IV: RERANKING & DRAFT LIST ──
    print("\n⏳ [4/5] Mengompilasi peringkat akhir...")
    alpha = 0.5
    survivor_scenes = [sc for sc in scenes if getattr(sc, "viral_label", 0) == 1]
    if not survivor_scenes:
        survivor_scenes = scenes
        
    scene_list = []
    for sc in survivor_scenes:
        rel_score = getattr(sc, "relevancy_score", 0.0)
        vir_score = getattr(sc, "viral_score", 0.0)
        vir_label = getattr(sc, "viral_label", 0)
        comb_score = alpha * rel_score + (1.0 - alpha) * vir_score
        
        # Smart Preview
        full_text = sc.text
        if len(full_text) <= 200:
            preview_text = full_text
        else:
            end_match = list(re.finditer(r"[.!?]\s", full_text[:200]))
            best_cut = -1
            for m in reversed(end_match):
                if m.end() > 60:
                    best_cut = m.end()
                    break
            if best_cut != -1:
                preview_text = full_text[:best_cut].strip()
            else:
                cut_idx = full_text.rfind(" ", 0, 200)
                preview_text = full_text[:cut_idx] + "..." if cut_idx > 0 else full_text[:197] + "..."
                
        scene_list.append({
            "start": sc.start,
            "end": sc.end,
            "duration": sc.duration,
            "text": preview_text,
            "relevancy_score": round(rel_score, 4),
            "viral_label": vir_label,
            "viral_score": round(vir_score, 4),
            "combined_score": round(comb_score, 4)
        })
        
    by_relevancy = sorted(scene_list, key=lambda x: x["relevancy_score"], reverse=True)[:5]
    by_virality = sorted(scene_list, key=lambda x: x["viral_score"], reverse=True)[:5]
    by_combined = sorted(scene_list, key=lambda x: x["combined_score"], reverse=True)[:5]
    
    output_data = {
        "global_summary": global_summary,
        "total_scenes": len(scenes),
        "rankings": {
            "by_relevancy": by_relevancy,
            "by_virality": by_virality,
            "by_combined": by_combined
        }
    }
    
    # Simpan JSON secara dinamis & standar
    scene_rankings_path = os.path.join(output_dir, f"{video_stem}_scene_rankings.json")
    with open(scene_rankings_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
        f.write("\n")
        
    std_json_path = os.path.join(SCRIPT_DIR, "scene_rankings.json")
    with open(std_json_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
        f.write("\n")
        
    print("✅ Berkas JSON Rankings Terbit!")

    # ── TAHAP V: ACCURATE PORTRAIT VIDEO CLIPPING DENGAN DYNAMIC SUBTITLE ──
    print("\n⏳ [5/5] Memulai pemotongan klip video fisik & rendering subtitle...")
    output_clips_dir = os.path.join(SCRIPT_DIR, "output_clips")
    
    import subtitle_generator
    
    # Fungsi pembungkus untuk memotong video dengan subtitle dinamis & portrait blur
    def process_and_cut_category(clips_data: list, category_name: str):
        category_dir = os.path.join(output_clips_dir, category_name)
        os.makedirs(category_dir, exist_ok=True)
        
        print(f"\n🎬 Memproses {len(clips_data)} adegan untuk kategori: '{category_name.upper()}'")
        for idx, clip in enumerate(clips_data, 1):
            start = clip["start"]
            end = clip["end"]
            duration = round(end - start, 2)
            
            # Cari adegan (Scene) asli di data scenes yang sesuai timestamps untuk mengambil list sentences (words)
            matching_scene = None
            for sc in scenes:
                if abs(sc.start - start) < 0.1 and abs(sc.end - end) < 0.1:
                    matching_scene = sc
                    break
                    
            if not matching_scene:
                # Fallback jika adegan tidak ditemukan
                sentences_data = [{"text": clip["text"], "start": start, "end": end, "words": []}]
            else:
                # Konversi list Segment dataclass ke format raw dict list
                sentences_data = []
                for s in matching_scene.sentences:
                    sentences_data.append({
                        "text": s.text,
                        "start": s.start,
                        "end": s.end,
                        "words": s.words
                    })
            
            # 1. Cari emoji dinamis dari Llama 3.2 3B
            print(f"   ➜ [{idx}/{len(clips_data)}] Menganalisis emoji untuk: \"{clip['text'][:40]}...\"")
            emoji = subtitle_generator.generate_emoji_for_text(clip["text"], pegasus_model, pegasus_tokenizer)
            
            # 2. Buat file ASS dinamis
            ass_filename = f"temp_clip_{category_name}_{idx}.ass"
            ass_path = os.path.join(output_dir, ass_filename)
            subtitle_generator.generate_ass_file(start, end, sentences_data, emoji, ass_path)
            
            # 3. Render video portrait + blur + subs via FFmpeg
            clean_text = "".join([c if c.isalnum() or c in " _-" else "" for c in clip["text"][:30]]).strip()
            clean_text = clean_text.replace(" ", "_")
            output_filename = f"clip_{idx:02d}_{start:.1f}s_{clean_text}.mp4"
            output_path = os.path.join(category_dir, output_filename)
            
            print(f"      💾 Rendering video vertikal 9:16 dengan Subtitle & Emoji...")
            success = subtitle_generator.render_portrait_video_with_subs(
                video_path,
                clean_audio_path,
                start,
                duration,
                ass_path,
                output_path
            )
            
            # Hapus file ASS sementara setelah selesai digunakan
            if os.path.exists(ass_path):
                os.remove(ass_path)
                
            if success:
                print("      ✅ Berhasil dirender!")
            else:
                print("      ❌ Gagal merender video.")

    # Jalankan pemrosesan video untuk ketiga kategori
    process_and_cut_category(by_combined, "combined")
    process_and_cut_category(by_relevancy, "relevancy")
    process_and_cut_category(by_virality, "virality")
    
    print("\n" + "=" * 60)
    print("  🎉 ALL PIPELINE PROCESSES COMPLETED SUCCESSFULLY!")
    print(f"  📂 Klip Video Portrait 9:16 Tersimpan di: {output_clips_dir}/")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    run_full_clipper_pipeline()
