"""CPU (Linux) replacements for the two Apple-only MLX components, so the same
pipeline runs on a free Hugging Face Space:

  * mlx_whisper            -> faster-whisper (CTranslate2)
  * mlx_lm Llama-3.2-3B    -> llama-cpp-python (GGUF Q4)

Both keep the exact contracts the rest of the code expects:
  * `faster_whisper_transcribe` returns the same dict shape as
    poc-audio-extraction/transcribe_pipeline.transcribe.
  * `LlamaCppTokenizer` exposes .encode/.decode so server.py's `_sentence_chunks`
    (token-budget chunker) works unchanged, and `llamacpp_chat` mirrors
    `_llama_generate`'s single-instruction chat call.

Heavy imports (faster_whisper, llama_cpp) are deferred into the functions so
importing this module stays cheap.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# faster-whisper accepts sizes ("base", "small", "large-v3", …) or a CT2 repo id.
# Map the app's model aliases onto faster-whisper's names.
_FW_SIZE_MAP = {
    "turbo": "large-v3-turbo",
    "large": "large-v3",
}


def faster_whisper_transcribe(path: str, model_name: Optional[str] = None) -> Dict[str, Any]:
    """Transcribe with faster-whisper on CPU, returning the transcribe_pipeline
    contract: {text, segments:[{id,start,end,text,words:[{word,start,end}]}],
    duration_s, speech_ratio, model}. faster-whisper's built-in VAD filter stands
    in for the MLX pipeline's separate preprocessing/VAD stage."""
    from faster_whisper import WhisperModel  # deferred heavy import

    name = (model_name or "base").strip()
    size = _FW_SIZE_MAP.get(name.lower(), name)
    compute = os.environ.get("TLDW_FW_COMPUTE", "int8")  # int8 = fastest on CPU
    model = WhisperModel(size, device="cpu", compute_type=compute)

    # `segments` is a generator — iterating it runs the actual transcription.
    segments, info = model.transcribe(path, word_timestamps=True, vad_filter=True)

    out_segments: List[Dict[str, Any]] = []
    texts: List[str] = []
    speech = 0.0
    for i, s in enumerate(segments):
        text = (s.text or "").strip()
        if not text:
            continue
        words = [{"word": (w.word or "").strip(),
                  "start": round(w.start, 3), "end": round(w.end, 3)}
                 for w in (s.words or [])]
        out_segments.append({"id": i, "start": round(s.start, 3),
                             "end": round(s.end, 3), "text": text, "words": words})
        texts.append(text)
        speech += max(0.0, float(s.end) - float(s.start))

    duration = float(getattr(info, "duration", 0.0) or 0.0)
    return {
        "text": " ".join(texts).strip(),
        "segments": out_segments,
        "duration_s": round(duration, 3),
        "speech_ratio": round((speech / duration) if duration else 0.0, 4),
        "model": f"faster-whisper/{size}",
    }


class LlamaCppTokenizer:
    """Adapts a llama_cpp.Llama to the tiny tokenizer surface server.py's
    `_sentence_chunks` needs: .encode(str)->list[int] and .decode(list[int])->str."""

    def __init__(self, llm: Any):
        self._llm = llm

    def encode(self, text: str, add_special_tokens: bool = False) -> List[int]:
        return self._llm.tokenize(text.encode("utf-8"), add_bos=False, special=False)

    def decode(self, ids: List[int]) -> str:
        return self._llm.detokenize(ids).decode("utf-8", "replace")


def load_llamacpp(repo_id: str, filename: str, n_ctx: int = 4096) -> Any:
    """Load a GGUF Llama on CPU via llama-cpp-python (downloads from HF on first use)."""
    from llama_cpp import Llama  # deferred heavy import
    return Llama.from_pretrained(
        repo_id=repo_id, filename=filename,
        n_ctx=n_ctx, verbose=False,
        n_threads=int(os.environ.get("TLDW_LLAMA_THREADS", "0")) or None,
    )


def llamacpp_chat(llm: Any, instruction: str, max_tokens: int) -> str:
    """One user instruction -> assistant text, mirroring _llama_generate."""
    out = llm.create_chat_completion(
        messages=[{"role": "user", "content": instruction}],
        max_tokens=max_tokens, temperature=0.7,
    )
    return (out["choices"][0]["message"]["content"] or "").strip()
