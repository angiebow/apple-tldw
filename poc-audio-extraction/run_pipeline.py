#!/usr/bin/env python3
"""
run_pipeline.py
===============
Script interaktif — tanya path video saat dijalankan,
lalu langsung proses dan tampilkan hasilnya.

Cara pakai:
    python run_pipeline.py
"""

import json
import os
import sys
from pathlib import Path

# ── pastikan root project ada di path ──────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audio_preprocessor import AudioPreprocessor
from audio_preprocessor.config import (
    DenoiseConfig,
    FFmpegConfig,
    PreprocessorConfig,
    VADConfig,
)
from audio_preprocessor.exceptions import AudioPreprocessorError


def divider(char="─", width=60):
    print(char * width)


def ask_path() -> str:
    """Tanya path video ke user, validasi file ada."""
    divider("═")
    print("  🎙  AUDIO PREPROCESSOR — Podcast Pipeline")
    divider("═")
    print()
    while True:
        raw = input("  📁  Path file video/audio kamu: ").strip()
        # hapus tanda kutip kalau di-drag dari Finder
        raw = raw.strip("'\"")
        if not raw:
            print("  ⚠️   Path tidak boleh kosong.\n")
            continue
        path = os.path.abspath(raw)
        if not os.path.isfile(path):
            print(f"  ❌  File tidak ditemukan: {path}\n")
            continue
        return path


def ask_output_dir() -> str:
    raw = input("  📂  Output folder [tekan Enter → ./output]: ").strip().strip("'\"")
    return raw if raw else "output"


def build_config(output_dir: str) -> PreprocessorConfig:
    return PreprocessorConfig(
        output_dir=output_dir,
        log_level="INFO",
        ffmpeg=FFmpegConfig(sample_rate=16_000, channels=1, codec="pcm_s16le"),
        denoise=DenoiseConfig(
            snr_clean_threshold=20.0,
            snr_mild_threshold=10.0,
        ),
        vad=VADConfig(
            threshold=0.5,
            min_speech_duration_ms=250,
            min_silence_duration_ms=500,
        ),
    )


def print_result(result) -> None:
    divider("═")
    print("  ✅  SELESAI!")
    divider("═")

    print(f"\n  📄  File output:")
    print(f"      🔊  Audio     : {result.preprocessed_audio_path}")
    print(f"      📋  VAD JSON  : {result.vad_metadata_path}")
    print(f"      📊  Report    : {result.report_path}")

    m = result.audio_metrics
    print(f"\n  📈  Kualitas Audio:")
    print(f"      Duration   : {m.duration_seconds:.1f} detik  ({m.duration_seconds/60:.1f} menit)")
    print(f"      SNR        : {m.snr_db:.1f} dB")
    print(f"      RMS        : {m.rms_db:.1f} dBFS")
    print(f"      Flatness   : {m.spectral_flatness:.3f}  (0=tonal, 1=noise)")
    print(f"      Clipping   : {m.clipping_ratio*100:.3f}%")

    d = result.denoise_decision
    print(f"\n  🔇  Denoising:")
    print(f"      Keputusan  : {'Ya' if d.should_denoise else 'Tidak'} ({d.strength})")
    if d.should_denoise and result.denoise_executed:
        print(f"      Dieksekusi : ✅ Ya — backend: {result.denoiser_backend}")
    elif d.should_denoise and not result.denoise_executed:
        print(f"      Dieksekusi : ⚠️  TIDAK — DeepFilterNet tidak terinstall, NullDenoiser digunakan")
        print(f"                   👉 Jalankan: pip install deepfilterlib")
    else:
        print(f"      Dieksekusi : ⏭  Tidak perlu (audio bersih)")
    print(f"      Alasan     : {d.reason}")

    print(f"\n  🎚️  Automatic Gain Control (AGC):")
    print(f"      Status     : {'✅ Aktif (Volume dinamis diratakan)' if result.agc_applied else '⏭  Tidak Aktif'}")

    v = result.vad_result
    print(f"\n  🎤  Voice Activity Detection:")
    print(f"      Segmen bicara  : {len(v.segments)}")
    print(f"      Total bicara   : {v.total_speech_duration:.1f} detik")
    print(f"      Rasio bicara   : {v.speech_ratio*100:.1f}%")

    if v.segments:
        print(f"\n  🕐  Pratinjau segmen pertama:")
        for i, seg in enumerate(v.segments[:5]):
            menit_s = int(seg.start // 60)
            detik_s = seg.start % 60
            menit_e = int(seg.end // 60)
            detik_e = seg.end % 60
            print(
                f"      [{i+1:>2}]  {menit_s:02d}:{detik_s:05.2f}  →  "
                f"{menit_e:02d}:{detik_e:05.2f}  ({seg.duration:.1f}s)"
            )
        if len(v.segments) > 5:
            print(f"      … dan {len(v.segments)-5} segmen lainnya")

    print(f"\n  ⏱   Waktu proses : {result.processing_time_seconds:.2f} detik")
    divider()

    # ── Integrasi Whisper ──────────────────────────────────────────────
    print("\n  💡  Cara pakai hasilnya dengan Whisper:")
    divider()
    print("""
    import whisper, json

    model = whisper.load_model("large-v3")

    with open("{vad}") as f:
        segments = json.load(f)["speech_segments"]

    for seg in segments:
        out = model.transcribe(
            "{wav}",
            clip_timestamps=[(seg["start"], seg["end"])]
        )
        print(out["text"])
    """.format(
        vad=result.vad_metadata_path,
        wav=result.preprocessed_audio_path,
    ))
    divider("═")


def main():
    input_path = ask_path()
    output_dir = ask_output_dir()
    print()

    config = build_config(output_dir)
    preprocessor = AudioPreprocessor(config)

    print()
    divider()
    print(f"  ⚙️   Memproses: {Path(input_path).name}")
    print(f"  📂  Output ke: {os.path.abspath(output_dir)}")
    divider()
    print()

    try:
        result = preprocessor.process(input_path)
    except FileNotFoundError as e:
        print(f"\n  ❌  File tidak ditemukan: {e}", file=sys.stderr)
        sys.exit(1)
    except AudioPreprocessorError as e:
        print(f"\n  ❌  Pipeline error: {e}", file=sys.stderr)
        sys.exit(2)

    print_result(result)


if __name__ == "__main__":
    main()
