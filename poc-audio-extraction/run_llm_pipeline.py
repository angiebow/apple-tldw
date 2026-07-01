#!/usr/bin/env python3
"""
run_llm_pipeline.py
===================
Executes the NLP/LLM pipeline to summarize transcripts, segment them into 
semantic scenes, and rank them using relevancy and virality scores.

Models & Tech Stack:
- Summarizer                : google/pegasus-xsum
- Embedding Model           : sentence-transformers/all-mpnet-base-v2
- Virality Classifier       : DistilBERT (binary classification)
- Virality Reranker (Reg)   : BERT-base (regression)
"""

import os
import sys
import json
import time
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Tuple
from pathlib import Path

import torch
import numpy as np
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM, AutoModelForSequenceClassification
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

# ─────────────────────────────────────────────────────────────────────────── #
# Data Structures                                                             #
# ─────────────────────────────────────────────────────────────────────────── #

@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str

@dataclass
class Scene:
    start: float
    end: float
    duration: float
    text: str
    sentences: List[Segment]

# ─────────────────────────────────────────────────────────────────────────── #
# Helper Functions                                                            #
# ─────────────────────────────────────────────────────────────────────────── #

def split_scene_recursive(
    scene_segments: List[Segment], 
    embeddings_map: Dict[int, np.ndarray], 
    max_duration: float = 60.0
) -> List[List[Segment]]:
    """
    Recursively splits a scene at its lowest semantic similarity point 
    if its duration exceeds the max_duration (e.g., 60 seconds).
    """
    duration = scene_segments[-1].end - scene_segments[0].start
    if duration <= max_duration:
        return [scene_segments]
        
    # If there is only one segment, we cannot split it further
    if len(scene_segments) <= 1:
        return [scene_segments]
        
    # Find the boundary with the lowest similarity score
    lowest_sim = float("inf")
    split_idx = -1
    
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
        
    # Split the segments into left and right sub-scenes
    left_part = scene_segments[:split_idx + 1]
    right_part = scene_segments[split_idx + 1:]
    
    return (split_scene_recursive(left_part, embeddings_map, max_duration) + 
            split_scene_recursive(right_part, embeddings_map, max_duration))

# ─────────────────────────────────────────────────────────────────────────── #
# Main Pipeline Function                                                      #
# ─────────────────────────────────────────────────────────────────────────── #

def run_llm_pipeline(
    transcript_path: str, 
    distilbert_path: str, 
    bert_path: str, 
    alpha: float = 0.5
) -> Dict[str, Any]:
    """
    Runs the complete 8-step NLP/LLM pipeline on the input transcript.
    
    Parameters:
        transcript_path: Path to the transcript_segments.json file.
        distilbert_path: Path to the local fine-tuned DistilBERT model.
        bert_path: Path to the local fine-tuned BERT-base regressor.
        alpha: Weight for combined score (combined = alpha * relevancy + (1 - alpha) * virality).
        
    Returns:
        Dictionary structured for the final JSON output.
    """
    start_time = time.perf_counter()
    print("═" * 60)
    print("  🚀 STARTING VIRALITY DETECTION PIPELINE")
    print("═" * 60)
    print(f"  📂 Transcript Path  : {transcript_path}")
    print(f"  🧠 DistilBERT Path  : {distilbert_path}")
    print(f"  🧠 BERT Path        : {bert_path}")
    print(f"  🎛️  Alpha Parameter  : {alpha}")
    print("═" * 60)

    # ───────────────────────────────────────────────────────────────────────
    # 0. Load Input Data & Setup Device
    # ───────────────────────────────────────────────────────────────────────
    if not os.path.isfile(transcript_path):
        raise FileNotFoundError(f"Transcript file not found: {transcript_path}")
        
    with open(transcript_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)
        
    if not raw_data:
        print("⚠️  Warning: Transcript file is empty.")
        return {"global_summary": "", "total_scenes": 0, "rankings": {"by_relevancy": [], "by_virality": [], "by_combined": []}}

    # Parse segments
    segments = [
        Segment(id=s["id"], start=s["start"], end=s["end"], text=s["text"]) 
        for s in raw_data
    ]
    print(f"✅ Loaded {len(segments)} segments.")

    device = "cpu"
    if torch.backends.mps.is_available():
        device = "mps"
    elif torch.cuda.is_available():
        device = "cuda"
    print(f"💻 Device configured: {device.upper()}")

    # ───────────────────────────────────────────────────────────────────────
    # 0.1 Load Models (once at the start)
    # ───────────────────────────────────────────────────────────────────────
    print("\n[0/8] Loading NLP & Deep Learning models…")
    model_load_start = time.perf_counter()
    
    # 1. PEGASUS Summarizer
    print("  📥 Loading PEGASUS tokenizer and model 'google/pegasus-xsum'…")
    pegasus_tokenizer = AutoTokenizer.from_pretrained("google/pegasus-xsum")
    pegasus_model = AutoModelForSeq2SeqLM.from_pretrained("google/pegasus-xsum").to(device)
    
    # 2. SentenceTransformer Embedding Model
    print("  📥 Loading SentenceTransformer model 'sentence-transformers/all-mpnet-base-v2'…")
    embedding_model = SentenceTransformer("sentence-transformers/all-mpnet-base-v2", device=device)
    
    # 3. DistilBERT Classifier (Binary)
    print(f"  📥 Loading DistilBERT Classifier from '{distilbert_path}'…")
    try:
        if distilbert_path and os.path.exists(distilbert_path):
            classifier_tokenizer = AutoTokenizer.from_pretrained(distilbert_path)
            classifier_model = AutoModelForSequenceClassification.from_pretrained(distilbert_path).to(device)
        else:
            print("  ⚠️  Local DistilBERT path not found. Falling back to public model 'distilbert-base-uncased-finetuned-sst-2-english'...")
            classifier_tokenizer = AutoTokenizer.from_pretrained("distilbert-base-uncased-finetuned-sst-2-english")
            classifier_model = AutoModelForSequenceClassification.from_pretrained("distilbert-base-uncased-finetuned-sst-2-english").to(device)
    except Exception as exc:
        print(f"  ⚠️  Failed to load local DistilBERT: {exc}. Falling back to public model...")
        classifier_tokenizer = AutoTokenizer.from_pretrained("distilbert-base-uncased-finetuned-sst-2-english")
        classifier_model = AutoModelForSequenceClassification.from_pretrained("distilbert-base-uncased-finetuned-sst-2-english").to(device)

    # 4. BERT Regressor (Sigmoid Score)
    print(f"  📥 Loading BERT Regressor from '{bert_path}'…")
    try:
        if bert_path and os.path.exists(bert_path):
            regressor_tokenizer = AutoTokenizer.from_pretrained(bert_path)
            regressor_model = AutoModelForSequenceClassification.from_pretrained(bert_path).to(device)
        else:
            print("  ⚠️  Local BERT path not found. Falling back to public model 'bert-base-uncased' (untrained regressor)...")
            regressor_tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")
            regressor_model = AutoModelForSequenceClassification.from_pretrained("bert-base-uncased", num_labels=1).to(device)
    except Exception as exc:
        print(f"  ⚠️  Failed to load local BERT Regressor: {exc}. Falling back to public model...")
        regressor_tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")
        regressor_model = AutoModelForSequenceClassification.from_pretrained("bert-base-uncased", num_labels=1).to(device)
        
    print(f"✅ Models loaded in {time.perf_counter() - model_load_start:.2f} s")

    # ───────────────────────────────────────────────────────────────────────
    # STEP 1 — Chunking (PEGASUS Tokenizer, max 800 tokens)
    # ───────────────────────────────────────────────────────────────────────
    print("\n[1/8] Splitting segments into semantic chunks (max 800 tokens)…")
    chunks: List[List[Segment]] = []
    current_chunk: List[Segment] = []
    current_tokens = 0
    
    for seg in segments:
        seg_tokens = len(pegasus_tokenizer.encode(seg.text, add_special_tokens=False))
        if current_tokens + seg_tokens > 800:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = [seg]
            current_tokens = seg_tokens
        else:
            current_chunk.append(seg)
            current_tokens += seg_tokens
            
    if current_chunk:
        chunks.append(current_chunk)
        
    print(f"✅ Created {len(chunks)} chunks.")

    # ───────────────────────────────────────────────────────────────────────
    # STEP 2 — Summarize Each Chunk (PEGASUS)
    # ───────────────────────────────────────────────────────────────────────
    print("\n[2/8] Generating summaries for each chunk…")
    chunk_summaries: List[str] = []
    for idx, chunk in enumerate(chunks):
        chunk_text = " ".join([s.text for s in chunk])
        
        inputs = pegasus_tokenizer(
            chunk_text, 
            truncation=True, 
            max_length=1024, 
            return_tensors="pt"
        ).to(device)
        
        with torch.no_grad():
            summary_ids = pegasus_model.generate(
                inputs["input_ids"],
                max_length=128,
                num_beams=4,
                length_penalty=2.0,
                early_stopping=True
            )
        summary = pegasus_tokenizer.decode(summary_ids[0], skip_special_tokens=True)
        chunk_summaries.append(summary)
        print(f"  📝 Chunk {idx+1}/{len(chunks)} Summary: {summary[:80]}…")
        
    # ───────────────────────────────────────────────────────────────────────
    # STEP 3 — Generate Global Summary (PEGASUS)
    # ───────────────────────────────────────────────────────────────────────
    print("\n[3/8] Generating overall global summary…")
    combined_chunk_summaries = " ".join(chunk_summaries)
    
    inputs = pegasus_tokenizer(
        combined_chunk_summaries, 
        truncation=True, 
        max_length=1024, 
        return_tensors="pt"
    ).to(device)
    
    with torch.no_grad():
        global_summary_ids = pegasus_model.generate(
            inputs["input_ids"],
            max_length=256,
            num_beams=4,
            length_penalty=2.0,
            early_stopping=True
        )
    global_summary = pegasus_tokenizer.decode(global_summary_ids[0], skip_special_tokens=True)
    print(f"✅ Global Summary: {global_summary}")

    # ───────────────────────────────────────────────────────────────────────
    # STEP 4 — Scene Detection (Semantic Segmentation, MPNet)
    # ───────────────────────────────────────────────────────────────────────
    print("\n[4/8] Segmenting transcript into scenes based on semantic similarity…")
    
    # Handle single segment edge-case
    if len(segments) <= 1:
        print("  ℹ️  Single segment or empty transcript. Skipping semantic boundary detection.")
        if segments:
            scenes = [Scene(
                start=segments[0].start,
                end=segments[0].end,
                duration=segments[0].end - segments[0].start,
                text=segments[0].text,
                sentences=segments
            )]
        else:
            scenes = []
    else:
        # Embed all sentences
        sentences_text = [seg.text for seg in segments]
        embeddings = embedding_model.encode(sentences_text, show_progress_bar=False)
        
        # Build map of segment_id -> embedding for fast retrieval in recursive split
        embeddings_map = {seg.id: embeddings[i] for i, seg in enumerate(segments)}
        
        # Calculate cosine similarities between consecutive sentences
        emb_a = embeddings[:-1]
        emb_b = embeddings[1:]
        a_norm = emb_a / np.linalg.norm(emb_a, axis=1, keepdims=True)
        b_norm = emb_b / np.linalg.norm(emb_b, axis=1, keepdims=True)
        similarities = np.sum(a_norm * b_norm, axis=1)
        
        # Calculate dynamic threshold
        mean_sim = np.mean(similarities)
        std_sim = np.std(similarities)
        threshold = mean_sim - 0.5 * std_sim
        print(f"  📊 Cosine Similarity Mean: {mean_sim:.3f} | Std: {std_sim:.3f} | Threshold: {threshold:.3f}")
        
        # Group sentences based on threshold boundaries
        scene_groups: List[List[Segment]] = []
        current_group = [segments[0]]
        
        for i in range(len(similarities)):
            if similarities[i] < threshold:
                # Boundary detected -> close current scene and start a new one
                scene_groups.append(current_group)
                current_group = [segments[i+1]]
            else:
                current_group.append(segments[i+1])
        if current_group:
            scene_groups.append(current_group)
            
        print(f"  🧩 Created {len(scene_groups)} initial scenes from boundaries.")
        
        # Enforce maximum 60-second limit recursively, and skip < 5-second scenes
        scenes: List[Scene] = []
        for group in scene_groups:
            # Recursively split large scenes
            split_groups = split_scene_recursive(group, embeddings_map, max_duration=60.0)
            
            for split_group in split_groups:
                start = split_group[0].start
                end = split_group[-1].end
                duration = end - start
                
                # Skip scenes shorter than 5 seconds
                if duration < 5.0:
                    continue
                    
                scene_text = " ".join([s.text for s in split_group])
                scenes.append(Scene(
                    start=round(start, 2),
                    end=round(end, 2),
                    duration=round(duration, 2),
                    text=scene_text,
                    sentences=split_group
                ))
                
    print(f"✅ Generated {len(scenes)} valid scenes (duration 5s - 60s).")

    # ───────────────────────────────────────────────────────────────────────
    # STEP 5 — Relevancy Scoring (all-mpnet-base-v2 vs Global Summary)
    # ───────────────────────────────────────────────────────────────────────
    print("\n[5/8] Calculating semantic relevancy scores against global summary…")
    if not scenes:
        print("⚠️  No scenes available for scoring.")
    else:
        # Embed global summary
        global_summary_emb = embedding_model.encode(global_summary, show_progress_bar=False).reshape(1, -1)
        scene_texts = [sc.text for sc in scenes]
        scene_embeddings = embedding_model.encode(scene_texts, show_progress_bar=False)
        
        # Calculate cosine similarities
        for idx, sc_emb in enumerate(scene_embeddings):
            sim = cosine_similarity(sc_emb.reshape(1, -1), global_summary_emb)[0][0]
            # Clip between 0.0 and 1.0 to map to target score range
            relevancy_score = float(np.clip(sim, 0.0, 1.0))
            object.__setattr__(scenes[idx], "relevancy_score", round(relevancy_score, 4))

    # ───────────────────────────────────────────────────────────────────────
    # STEP 6 — Virality Classification (DistilBERT Classifier)
    # ───────────────────────────────────────────────────────────────────────
    print("\n[6/8] Running DistilBERT binary virality classifier…")
    for idx, scene in enumerate(scenes):
        inputs = classifier_tokenizer(
            scene.text, 
            truncation=True, 
            max_length=512, 
            return_tensors="pt"
        ).to(device)
        
        with torch.no_grad():
            outputs = classifier_model(**inputs)
            logits = outputs.logits
            viral_label = int(logits.argmax(dim=-1).item())
            
        object.__setattr__(scene, "viral_label", viral_label)

    # ───────────────────────────────────────────────────────────────────────
    # STEP 7 — Virality Reranking (BERT Regression with Sigmoid)
    # ───────────────────────────────────────────────────────────────────────
    print("\n[7/8] Running BERT regression model for fine-grained virality score…")
    for idx, scene in enumerate(scenes):
        inputs = regressor_tokenizer(
            scene.text, 
            truncation=True, 
            max_length=512, 
            return_tensors="pt"
        ).to(device)
        
        with torch.no_grad():
            outputs = regressor_model(**inputs)
            logits = outputs.logits
            raw_score = logits[0, 0].item()
            # Sigmoid normalization (0.0 to 1.0)
            viral_score = 1.0 / (1.0 + np.exp(-raw_score))
            
        object.__setattr__(scene, "viral_score", round(viral_score, 4))

    # ───────────────────────────────────────────────────────────────────────
    # STEP 8 — Final Ranking (Combined score: alpha*relevancy + (1-alpha)*virality)
    # ───────────────────────────────────────────────────────────────────────
    print("\n[8/8] Calculating combined scores and sorting final rankings…")
    scene_list: List[Dict[str, Any]] = []
    
    for idx, sc in enumerate(scenes):
        # Read scores attached dynamically
        rel_score = getattr(sc, "relevancy_score", 0.0)
        vir_score = getattr(sc, "viral_score", 0.0)
        vir_label = getattr(sc, "viral_label", 0)
        
        comb_score = alpha * rel_score + (1.0 - alpha) * vir_score
        
        scene_list.append({
            "start": sc.start,
            "end": sc.end,
            "duration": sc.duration,
            "text": sc.text[:100],  # Limit text to first 100 characters
            "relevancy_score": round(rel_score, 4),
            "viral_label": vir_label,
            "viral_score": round(vir_score, 4),
            "combined_score": round(comb_score, 4)
        })

    # Sort rankings descending
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

    # Save to scene_rankings.json (using dynamic prefix if output_dir is known)
    output_dir = os.path.dirname(transcript_path) if os.path.dirname(transcript_path) else "output"
    audio_stem = Path(transcript_path).stem
    if audio_stem.endswith("_transcript_segments"):
        base_name = audio_stem[:-20]
    else:
        base_name = audio_stem
        
    scene_rankings_path = os.path.join(output_dir, f"{base_name}_scene_rankings.json")
    
    with open(scene_rankings_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
        f.write("\n")
        
    # Also save a standard non-prefixed file as requested by the spec
    std_path = "scene_rankings.json"
    with open(std_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    elapsed_time = time.perf_counter() - start_time
    print("═" * 60)
    print("  🎉 SUCCESS!")
    print("═" * 60)
    print(f"  📄 Dynamic Output : {scene_rankings_path}")
    print(f"  📄 Standard Output: {std_path}")
    print(f"  ⏱  Pipeline Time  : {elapsed_time:.2f} s")
    print("═" * 60)

    return output_data

if __name__ == "__main__":
    # If run directly, offer a quick CLI usage guide
    if len(sys.argv) < 2:
        print("Usage: python run_llm_pipeline.py <transcript_segments.json_path> [distilbert_path] [bert_path]")
        sys.exit(1)
        
    tx_path = sys.argv[1]
    db_path = sys.argv[2] if len(sys.argv) > 2 else ""
    b_path = sys.argv[3] if len(sys.argv) > 3 else ""
    
    run_llm_pipeline(tx_path, db_path, b_path)
