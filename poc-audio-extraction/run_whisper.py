#!/usr/bin/env python3
"""
run_whisper.py
==============
Runs OpenAI Whisper Speech-to-Text on preprocessed audio, guided by VAD segments.

Features:
  - Automatically uses Apple Silicon GPU (MPS) acceleration if available.
  - Guided transcription: slices audio based on VAD metadata to speed up
    inference and eliminate hallucinations in silent zones.
  - Outputs two separate JSON files: segment-level and word-level.
"""

import os
import sys
import json
import argparse
import time
from pathlib import Path
import torch
import torchaudio
import whisper

def get_mlx_model_path(model_name: str) -> str:
    """Maps simple Whisper model names to standard MLX-community Hugging Face repos."""
    if "/" in model_name:
        return model_name
    
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
        "large-v1": "mlx-community/whisper-large-v1-mlx",
        "large-v2": "mlx-community/whisper-large-v2-mlx",
        "large-v3": "mlx-community/whisper-large-v3-mlx",
        "turbo": "mlx-community/whisper-large-v3-turbo"
    }
    return mapping.get(model_name.lower(), f"mlx-community/whisper-{model_name}-mlx")

def parse_args():
    parser = argparse.ArgumentParser(description="Run Whisper STT guided by VAD segments.")
    parser.add_argument(
        "--audio",
        default="output/preprocessed.wav",
        help="Path to the preprocessed WAV file (default: output/preprocessed.wav)"
    )
    parser.add_argument(
        "--vad",
        default="output/vad_metadata.json",
        help="Path to the VAD metadata JSON file (default: output/vad_metadata.json)"
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory to save transcription JSON outputs (default: output)"
    )
    parser.add_argument(
        "--model",
        default="medium",
        help="Whisper model size to use (default: medium)"
    )
    parser.add_argument(
        "--backend",
        choices=["openai", "mlx"],
        default="mlx",
        help="Backend engine to use: 'openai' (PyTorch) or 'mlx' (Apple Silicon optimized) (default: mlx)"
    )
    return parser.parse_args()

def main():
    args = parse_args()
    
    # 1. Validate inputs
    if not os.path.isfile(args.audio):
        print(f"❌ Audio file not found: {args.audio}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(args.vad):
        print(f"❌ VAD metadata file not found: {args.vad}", file=sys.stderr)
        sys.exit(1)
        
    os.makedirs(args.output_dir, exist_ok=True)
    
    print("═" * 60)
    print("  🎙️  WHISPER STT — Transcription Pipeline")
    print("═" * 60)
    print(f"  🔊 Audio input   : {args.audio}")
    print(f"  📋 VAD segments  : {args.vad}")
    print(f"  📂 Output folder : {args.output_dir}")
    print(f"  🧠 Model size    : {args.model}")
    
    # 2. Select and validate Backend & Hardware
    backend = args.backend.lower()
    has_mlx = False
    try:
        import mlx.core as mx
        import mlx_whisper
        has_mlx = True
    except ImportError:
        pass

    if backend == "mlx" and not has_mlx:
        print("⚠️  MLX backend selected but 'mlx-whisper' or 'mlx' is not installed.")
        print("🔄 Falling back to OpenAI (PyTorch) backend.")
        backend = "openai"

    device = "cpu"
    if backend == "openai":
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"

    print(f"  💻 Backend engine: {backend.upper()}")
    if backend == "openai":
        print(f"  💻 Hardware      : {device.upper()}")
    print("═" * 60)
    
    # 3. Load Whisper model
    load_start = time.perf_counter()
    if backend == "mlx":
        mlx_model_path = get_mlx_model_path(args.model)
        print(f"\n[1/4] Loading MLX Whisper model '{mlx_model_path}' (this may take a moment)…")
        from mlx_whisper.transcribe import ModelHolder
        # Pre-load/download the model to warm up cache and measure loading time
        model = ModelHolder.get_model(mlx_model_path, mx.float16)
    else:
        print(f"\n[1/4] Loading OpenAI Whisper model '{args.model}' (this may take a moment)…")
        try:
            model = whisper.load_model(args.model, device=device)
        except (NotImplementedError, RuntimeError) as exc:
            if device == "mps":
                print(f"\n⚠️  MPS model loading failed: {exc}")
                print("🔄 Falling back to CPU. Reloading model on CPU (this might take a moment)…")
                device = "cpu"
                model = whisper.load_model(args.model, device="cpu")
            else:
                raise exc
    print(f"✅ Model loaded successfully in {time.perf_counter() - load_start:.2f} s")
    
    # 4. Load Audio and VAD Metadata
    print("\n[2/4] Loading audio and VAD segments…")
    waveform, sample_rate = torchaudio.load(args.audio)
    if sample_rate != 16000:
        print(f"⚠️  Warning: Expected sample rate 16000 Hz, got {sample_rate} Hz.")
        
    with open(args.vad, "r", encoding="utf-8") as f:
        vad_data = json.load(f)
    segments = vad_data.get("speech_segments", [])
    print(f"✅ Loaded audio ({waveform.shape[1]/sample_rate:.1f} s) and {len(segments)} speech segments.")
    
    # 5. Process Transcription Segment-by-Segment
    print("\n[3/4] Transcribing speech segments (word-level timestamps enabled)…")
    global_segments = []
    global_words = []
    segment_id = 0
    
    transcribe_start = time.perf_counter()
    for idx, seg in enumerate(segments):
        start_time = seg["start"]
        end_time = seg["end"]
        duration = end_time - start_time
        
        print(f"  ⏳ [{idx+1}/{len(segments)}] Transcribing {start_time:.2f}s - {end_time:.2f}s ({duration:.1f}s)…")
        
        # Slice the audio waveform
        start_sample = int(start_time * sample_rate)
        end_sample = int(end_time * sample_rate)
        segment_audio = waveform[0, start_sample:end_sample].numpy()
        
        # Skip if slice is empty or too short (Whisper expects at least some samples)
        if len(segment_audio) < 160:
            continue
            
        # Transcribe this chunk.
        if backend == "mlx":
            res = mlx_whisper.transcribe(
                segment_audio.astype("float32"),
                path_or_hf_repo=mlx_model_path,
                word_timestamps=True,
                verbose=False
            )
        else:
            try:
                res = model.transcribe(segment_audio, word_timestamps=True, fp16=False)
            except (NotImplementedError, RuntimeError) as exc:
                if device == "mps":
                    print(f"\n⚠️  MPS execution failed: {exc}")
                    print("🔄 Falling back to CPU. Reloading model on CPU (this might take a moment)…")
                    device = "cpu"
                    model = whisper.load_model(args.model, device="cpu")
                    res = model.transcribe(segment_audio, word_timestamps=True, fp16=False)
                else:
                    raise exc
        
        # Extract and offset timestamps
        for s in res.get("segments", []):
            global_segments.append({
                "id": segment_id,
                "start": round(start_time + s["start"], 3),
                "end": round(start_time + s["end"], 3),
                "text": s["text"].strip()
            })
            segment_id += 1
            
            for w in s.get("words", []):
                global_words.append({
                    "word": w["word"].strip(),
                    "start": round(start_time + w["start"], 3),
                    "end": round(start_time + w["end"], 3),
                    "probability": round(w["probability"], 4)
                })
                
    elapsed = time.perf_counter() - transcribe_start
    print(f"✅ Transcription complete in {elapsed:.2f} s")
    
    # 6. Save JSON Outputs
    print("\n[4/4] Saving output files…")
    
    # Dapatkan nama dasar dari file audio input untuk menghindari tabrakan nama file
    audio_stem = Path(args.audio).stem
    if audio_stem.endswith("_preprocessed"):
        base_name = audio_stem[:-13]  # Potong akhiran "_preprocessed"
    else:
        base_name = audio_stem
        
    segments_path = os.path.join(args.output_dir, f"{base_name}_transcript_segments.json")
    words_path = os.path.join(args.output_dir, f"{base_name}_transcript_words.json")
    
    with open(segments_path, "w", encoding="utf-8") as f:
        json.dump(global_segments, f, indent=2, ensure_ascii=False)
        f.write("\n")
        
    with open(words_path, "w", encoding="utf-8") as f:
        json.dump(global_words, f, indent=2, ensure_ascii=False)
        f.write("\n")
        
    print("═" * 60)
    print("  🎉 SUCCESS!")
    print("═" * 60)
    print(f"  📄 Segments JSON : {segments_path}")
    print(f"  📄 Words JSON    : {words_path}")
    print("═" * 60)

if __name__ == "__main__":
    main()
