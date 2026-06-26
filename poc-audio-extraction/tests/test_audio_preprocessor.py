"""
Unit Tests – Audio Preprocessor
================================
Tests follow the Arrange-Act-Assert (AAA) pattern and use ``unittest.mock``
heavily so that no GPU, FFmpeg binary, or network access is needed in CI.

Test coverage targets:
    • config.py           – default values, output_path helper
    • models.py           – to_dict(), to_report_dict(), to_vad_dict()
    • exceptions.py       – error message content
    • extractor.py        – command construction, subprocess failure, ext validation
    • quality.py          – all four metrics, edge cases (silence, clipping)
    • denoiser.py         – decide_denoising() policy, NullDenoiser copy
    • vad.py              – segment conversion, sample-to-second mapping
    • writer.py           – JSON content validation, dir creation
    • preprocessor.py     – full pipeline with mocked stages
"""

from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np


# ============================================================ #
#  Helpers                                                     #
# ============================================================ #


def _write_wav(path: str, samples: np.ndarray, sample_rate: int = 16_000) -> str:
    """Write a 16-bit mono WAV file from a float32 NumPy array."""
    pcm = (samples * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return path


def _sine_wave(
    frequency: float = 440.0,
    duration: float = 2.0,
    amplitude: float = 0.5,
    sample_rate: int = 16_000,
) -> np.ndarray:
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


# ============================================================ #
#  1. Config                                                   #
# ============================================================ #


class TestPreprocessorConfig(unittest.TestCase):
    def test_defaults(self):
        from audio_preprocessor.config import PreprocessorConfig

        cfg = PreprocessorConfig()
        self.assertEqual(cfg.output_dir, "output")
        self.assertEqual(cfg.ffmpeg.sample_rate, 16_000)
        self.assertEqual(cfg.ffmpeg.channels, 1)
        self.assertEqual(cfg.vad.threshold, 0.5)
        self.assertEqual(cfg.denoise.snr_clean_threshold, 20.0)

    def test_output_path_is_absolute(self):
        from audio_preprocessor.config import PreprocessorConfig

        cfg = PreprocessorConfig(output_dir="relative/path")
        self.assertTrue(os.path.isabs(cfg.output_path))

    def test_resolve_output(self):
        from audio_preprocessor.config import PreprocessorConfig

        cfg = PreprocessorConfig(output_dir="/tmp/test_out")
        self.assertEqual(cfg.resolve_output("foo.wav"), "/tmp/test_out/foo.wav")

    def test_custom_denoise_thresholds(self):
        from audio_preprocessor.config import DenoiseConfig

        dcfg = DenoiseConfig(snr_clean_threshold=25.0, snr_mild_threshold=15.0)
        self.assertEqual(dcfg.snr_clean_threshold, 25.0)
        self.assertEqual(dcfg.snr_mild_threshold, 15.0)

    def test_normalization_config_defaults(self):
        from audio_preprocessor.config import NormalizationConfig

        ncfg = NormalizationConfig()
        self.assertTrue(ncfg.enabled)
        self.assertEqual(ncfg.target_lufs, -23.0)
        self.assertGreater(ncfg.max_peak, 0.95)


# ============================================================ #
#  2. Models                                                   #
# ============================================================ #


class TestModels(unittest.TestCase):
    def _make_metrics(self):
        from audio_preprocessor.models import AudioMetrics

        return AudioMetrics(
            snr_db=18.5,
            rms_db=-20.1,
            spectral_flatness=0.12,
            clipping_ratio=0.001,
            duration_seconds=120.0,
            sample_rate=16_000,
            num_channels=1,
        )

    def test_audio_metrics_to_dict(self):
        m = self._make_metrics()
        d = m.to_dict()
        self.assertEqual(d["snr_db"], 18.5)
        self.assertIn("rms_db", d)

    def test_vad_segment_duration(self):
        from audio_preprocessor.models import VADSegment

        seg = VADSegment(start=10.0, end=25.5)
        self.assertAlmostEqual(seg.duration, 15.5)

    def test_vad_segment_to_dict(self):
        from audio_preprocessor.models import VADSegment

        seg = VADSegment(start=10.1234, end=25.5678)
        d = seg.to_dict()
        self.assertIn("start", d)
        self.assertIn("end", d)

    def test_preprocessing_result_denoise_applied_alias(self):
        from audio_preprocessor.models import (
            AudioMetrics,
            DenoiseDecision,
            NormalizationResult,
            PreprocessingResult,
            VADResult,
        )

        decision = DenoiseDecision(
            should_denoise=True, strength="mild", reason="test", snr_at_decision=15.0
        )
        norm = NormalizationResult(
            input_lufs=-28.0, output_lufs=-23.0, target_lufs=-23.0,
            gain_db=5.0, clipping_prevented=False, normalization_applied=True,
        )
        result = PreprocessingResult(
            preprocessed_audio_path="/out/preprocessed.wav",
            vad_metadata_path="/out/vad.json",
            report_path="/out/report.json",
            audio_metrics=self._make_metrics(),
            denoise_decision=decision,
            normalization_result=norm,
            vad_result=VADResult(segments=[], total_speech_duration=0, speech_ratio=0),
            processing_time_seconds=3.14,
            input_path="/input/podcast.mp4",
            denoise_executed=True,
            denoiser_backend="DeepFilterNetDenoiser",
        )
        self.assertTrue(result.denoise_applied)

    def test_report_dict_contains_required_keys(self):
        from audio_preprocessor.models import (
            AudioMetrics,
            DenoiseDecision,
            NormalizationResult,
            PreprocessingResult,
            VADResult,
        )

        decision = DenoiseDecision(
            should_denoise=False, strength="none", reason="clean", snr_at_decision=25.0
        )
        norm = NormalizationResult(
            input_lufs=-28.0, output_lufs=-23.0, target_lufs=-23.0,
            gain_db=5.0, clipping_prevented=False, normalization_applied=True,
        )
        result = PreprocessingResult(
            preprocessed_audio_path="/out/preprocessed.wav",
            vad_metadata_path="/out/vad.json",
            report_path="/out/report.json",
            audio_metrics=self._make_metrics(),
            denoise_decision=decision,
            normalization_result=norm,
            vad_result=VADResult(segments=[], total_speech_duration=0, speech_ratio=0),
            processing_time_seconds=5.0,
            input_path="/input/podcast.mp4",
        )
        report = result.to_report_dict()
        for key in ("audio_metrics", "denoising", "processing_time_seconds", "normalization"):
            self.assertIn(key, report)
        self.assertIn("decision", report["denoising"])
        self.assertIn("executed", report["denoising"])


# ============================================================ #
#  3. Exceptions                                               #
# ============================================================ #


class TestExceptions(unittest.TestCase):
    def test_unsupported_format_error(self):
        from audio_preprocessor.exceptions import UnsupportedFormatError

        err = UnsupportedFormatError("/foo/bar.xyz", (".mp4", ".wav"))
        self.assertIn("bar.xyz", str(err))
        self.assertIn(".mp4", str(err))

    def test_ffmpeg_error(self):
        from audio_preprocessor.exceptions import FFmpegError

        err = FFmpegError("ffmpeg -i x.mp4 out.wav", returncode=1, stderr="pipe error")
        self.assertIn("pipe error", str(err))
        self.assertEqual(err.returncode, 1)

    def test_ffmpeg_not_found_error(self):
        from audio_preprocessor.exceptions import FFmpegNotFoundError

        err = FFmpegNotFoundError("/usr/bin/ffmpeg")
        self.assertIn("/usr/bin/ffmpeg", str(err))


# ============================================================ #
#  4. FFmpegExtractor                                          #
# ============================================================ #


class TestFFmpegExtractor(unittest.TestCase):
    def _make_extractor(self, ffmpeg_path: str = "ffmpeg"):
        from audio_preprocessor.config import FFmpegConfig
        from audio_preprocessor.extractor import FFmpegExtractor

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            return FFmpegExtractor(FFmpegConfig(ffmpeg_path=ffmpeg_path))

    def test_build_command_contains_codec_and_rate(self):
        extractor = self._make_extractor()
        cmd = extractor._build_command("/in/pod.mp4", "/out/out.wav")
        self.assertIn("-acodec", cmd)
        self.assertIn("pcm_s16le", cmd)
        self.assertIn("-ar", cmd)
        self.assertIn("16000", cmd)
        self.assertIn("-vn", cmd)
        self.assertIn("-y", cmd)  # overwrite flag

    def test_unsupported_extension_raises(self):
        from audio_preprocessor.exceptions import UnsupportedFormatError

        extractor = self._make_extractor()
        with self.assertRaises(UnsupportedFormatError):
            extractor.extract("/path/to/file.xyz", "/out/out.wav")

    def test_ffmpeg_failure_raises_ffmpeg_error(self):
        from audio_preprocessor.exceptions import FFmpegError

        extractor = self._make_extractor()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "invalid data"
        with tempfile.TemporaryDirectory() as tmp:
            with patch("subprocess.run", return_value=mock_result):
                with self.assertRaises(FFmpegError):
                    extractor.extract("/path/to/pod.mp4", os.path.join(tmp, "out.wav"))

    def test_ffmpeg_not_found_raises(self):
        from audio_preprocessor.config import FFmpegConfig
        from audio_preprocessor.exceptions import FFmpegNotFoundError
        from audio_preprocessor.extractor import FFmpegExtractor

        with self.assertRaises(FFmpegNotFoundError):
            with patch("shutil.which", return_value=None):
                FFmpegExtractor(FFmpegConfig(ffmpeg_path="ffmpeg"))

    def test_supported_video_extensions_accepted(self):
        from audio_preprocessor.extractor import VIDEO_EXTENSIONS

        self.assertIn(".mp4", VIDEO_EXTENSIONS)
        self.assertIn(".mov", VIDEO_EXTENSIONS)

    def test_supported_audio_extensions_accepted(self):
        from audio_preprocessor.extractor import AUDIO_EXTENSIONS

        self.assertIn(".wav", AUDIO_EXTENSIONS)
        self.assertIn(".mp3", AUDIO_EXTENSIONS)


# ============================================================ #
#  5. AudioQualityAssessor                                     #
# ============================================================ #


class TestAudioQualityAssessor(unittest.TestCase):
    def setUp(self):
        from audio_preprocessor.config import QualityConfig
        from audio_preprocessor.quality import AudioQualityAssessor

        self.assessor = AudioQualityAssessor(QualityConfig())

    def _assess_sine(self, amplitude=0.5, duration=3.0) -> "AudioMetrics":  # noqa: F821
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            path = tf.name
        try:
            samples = _sine_wave(amplitude=amplitude, duration=duration)
            _write_wav(path, samples)
            return self.assessor.assess(path)
        finally:
            os.unlink(path)

    def test_metrics_are_returned(self):
        metrics = self._assess_sine()
        self.assertIsNotNone(metrics.snr_db)
        self.assertIsNotNone(metrics.rms_db)
        self.assertIsNotNone(metrics.spectral_flatness)
        self.assertIsNotNone(metrics.clipping_ratio)

    def test_duration_approximately_correct(self):
        metrics = self._assess_sine(duration=3.0)
        self.assertAlmostEqual(metrics.duration_seconds, 3.0, delta=0.1)

    def test_sample_rate_stored(self):
        metrics = self._assess_sine()
        self.assertEqual(metrics.sample_rate, 16_000)

    def test_clipping_ratio_near_zero_for_normal_audio(self):
        metrics = self._assess_sine(amplitude=0.5)
        self.assertLess(metrics.clipping_ratio, 0.01)

    def test_clipping_ratio_high_for_clipped_audio(self):
        samples = np.ones(32_000, dtype=np.float32)  # fully clipped
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            path = tf.name
        try:
            _write_wav(path, samples)
            metrics = self.assessor.assess(path)
        finally:
            os.unlink(path)
        self.assertGreater(metrics.clipping_ratio, 0.5)

    def test_rms_db_is_negative(self):
        metrics = self._assess_sine(amplitude=0.5)
        self.assertLess(metrics.rms_db, 0)

    def test_spectral_flatness_sine_is_low(self):
        """A pure sine wave is maximally tonal → low flatness."""
        metrics = self._assess_sine()
        self.assertLess(metrics.spectral_flatness, 0.5)

    def test_file_not_found_raises_audio_load_error(self):
        from audio_preprocessor.exceptions import AudioLoadError

        with self.assertRaises(AudioLoadError):
            self.assessor.assess("/nonexistent/path/audio.wav")


# ============================================================ #
#  6. Denoiser – decide_denoising()                           #
# ============================================================ #


class TestDecideDenoising(unittest.TestCase):
    def setUp(self):
        from audio_preprocessor.config import DenoiseConfig
        from audio_preprocessor.models import AudioMetrics

        self.cfg = DenoiseConfig(snr_clean_threshold=20.0, snr_mild_threshold=10.0)

        def _m(snr: float, flatness: float = 0.1) -> AudioMetrics:
            return AudioMetrics(
                snr_db=snr, rms_db=-20.0, spectral_flatness=flatness,
                clipping_ratio=0.0, duration_seconds=60.0,
                sample_rate=16_000, num_channels=1,
            )
        self._m = _m

    def test_clean_audio_no_denoise(self):
        from audio_preprocessor.denoiser import decide_denoising
        decision = decide_denoising(self._m(25.0), self.cfg)
        self.assertFalse(decision.should_denoise)
        self.assertEqual(decision.strength, "none")

    def test_mild_noise_mild_denoise(self):
        from audio_preprocessor.denoiser import decide_denoising
        decision = decide_denoising(self._m(15.0), self.cfg)
        self.assertTrue(decision.should_denoise)
        self.assertEqual(decision.strength, "mild")

    def test_heavy_noise_strong_denoise(self):
        from audio_preprocessor.denoiser import decide_denoising
        decision = decide_denoising(self._m(5.0), self.cfg)
        self.assertTrue(decision.should_denoise)
        self.assertEqual(decision.strength, "strong")

    def test_exactly_at_clean_threshold_no_denoise(self):
        from audio_preprocessor.denoiser import decide_denoising
        decision = decide_denoising(self._m(20.0), self.cfg)
        self.assertFalse(decision.should_denoise)

    def test_exactly_at_mild_threshold_mild_denoise(self):
        from audio_preprocessor.denoiser import decide_denoising
        decision = decide_denoising(self._m(10.0), self.cfg)
        self.assertTrue(decision.should_denoise)
        self.assertEqual(decision.strength, "mild")

    def test_force_denoise_overrides_clean_snr(self):
        from audio_preprocessor.config import DenoiseConfig
        from audio_preprocessor.denoiser import decide_denoising
        cfg = DenoiseConfig(force_denoise=True)
        decision = decide_denoising(self._m(30.0), cfg)
        self.assertTrue(decision.should_denoise)
        self.assertEqual(decision.strength, "strong")

    def test_reason_is_non_empty(self):
        from audio_preprocessor.denoiser import decide_denoising
        for snr in (5.0, 15.0, 25.0):
            decision = decide_denoising(self._m(snr), self.cfg)
            self.assertTrue(len(decision.reason) > 0, f"Empty reason for SNR={snr}")

    def test_snr_at_decision_stored(self):
        from audio_preprocessor.denoiser import decide_denoising
        decision = decide_denoising(self._m(12.3), self.cfg)
        self.assertAlmostEqual(decision.snr_at_decision, 12.3)

    def test_high_flatness_triggers_denoise_even_with_good_snr(self):
        """High spectral flatness should trigger denoising even when SNR is clean."""
        from audio_preprocessor.denoiser import decide_denoising
        from audio_preprocessor.config import DenoiseConfig
        cfg = DenoiseConfig(
            snr_clean_threshold=20.0,
            flatness_clean_threshold=0.3,
            flatness_noisy_threshold=0.6,
        )
        # Good SNR but very noisy-looking spectrum
        decision = decide_denoising(self._m(snr=25.0, flatness=0.8), cfg)
        self.assertTrue(decision.should_denoise)

    def test_clipping_note_in_reason(self):
        """A clipped audio file should have a warning in the reason string."""
        from audio_preprocessor.denoiser import decide_denoising
        from audio_preprocessor.config import DenoiseConfig
        from audio_preprocessor.models import AudioMetrics
        cfg = DenoiseConfig(clipping_warn_threshold=0.005)
        metrics = AudioMetrics(
            snr_db=25.0, rms_db=-10.0, spectral_flatness=0.1,
            clipping_ratio=0.02,   # 2% clipping -> above threshold
            duration_seconds=60.0, sample_rate=16_000, num_channels=1,
        )
        decision = decide_denoising(metrics, cfg)
        self.assertIn("CLIPPING", decision.reason)


class TestNullDenoiser(unittest.TestCase):
    def test_copies_file(self):
        from audio_preprocessor.denoiser import NullDenoiser
        from audio_preprocessor.config import DenoiseStrength

        denoiser = NullDenoiser()
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.wav")
            dst = os.path.join(tmp, "dst.wav")
            # Write a minimal WAV.
            _write_wav(src, _sine_wave())
            denoiser.denoise(src, dst, DenoiseStrength.MILD)
            self.assertTrue(os.path.isfile(dst))

    def test_is_available_always_true(self):
        from audio_preprocessor.denoiser import NullDenoiser

        self.assertTrue(NullDenoiser().is_available)


# ============================================================ #
#  7. VAD – segment conversion                                 #
# ============================================================ #


class TestSileroVADHelpers(unittest.TestCase):
    def test_convert_segments_sample_to_seconds(self):
        from audio_preprocessor.vad import SileroVAD

        raw = [{"start": 16_000, "end": 48_000}]  # 1s–3s at 16kHz
        segs = SileroVAD._convert_segments(raw, sample_rate=16_000)
        self.assertEqual(len(segs), 1)
        self.assertAlmostEqual(segs[0].start, 1.0)
        self.assertAlmostEqual(segs[0].end, 3.0)

    def test_segments_sorted_by_start(self):
        from audio_preprocessor.vad import SileroVAD

        raw = [
            {"start": 32_000, "end": 48_000},
            {"start": 0, "end": 16_000},
        ]
        segs = SileroVAD._convert_segments(raw, sample_rate=16_000)
        self.assertLess(segs[0].start, segs[1].start)

    def test_empty_segments(self):
        from audio_preprocessor.vad import SileroVAD

        segs = SileroVAD._convert_segments([], sample_rate=16_000)
        self.assertEqual(segs, [])


# ============================================================ #
#  8. OutputWriter                                             #
# ============================================================ #


class TestOutputWriter(unittest.TestCase):
    def _make_result(self, tmp_dir: str):
        from audio_preprocessor.models import (
            AudioMetrics,
            DenoiseDecision,
            PreprocessingResult,
            VADResult,
            VADSegment,
        )

        metrics = AudioMetrics(
            snr_db=22.0,
            rms_db=-18.5,
            spectral_flatness=0.08,
            clipping_ratio=0.0,
            duration_seconds=60.0,
            sample_rate=16_000,
            num_channels=1,
        )
        decision = DenoiseDecision(
            should_denoise=False,
            strength="none",
            reason="clean audio",
            snr_at_decision=22.0,
        )
        vad = VADResult(
            segments=[VADSegment(start=0.5, end=30.2)],
            total_speech_duration=29.7,
            speech_ratio=0.495,
        )
        from audio_preprocessor.models import NormalizationResult
        norm = NormalizationResult(
            input_lufs=-28.0, output_lufs=-23.0, target_lufs=-23.0,
            gain_db=5.0, clipping_prevented=False, normalization_applied=True,
        )
        return PreprocessingResult(
            preprocessed_audio_path=os.path.join(tmp_dir, "preprocessed.wav"),
            vad_metadata_path=os.path.join(tmp_dir, "vad_metadata.json"),
            report_path=os.path.join(tmp_dir, "processing_report.json"),
            audio_metrics=metrics,
            denoise_decision=decision,
            normalization_result=norm,
            vad_result=vad,
            processing_time_seconds=2.5,
            input_path="/podcast.mp4",
        )

    def test_output_files_created(self):
        from audio_preprocessor.config import PreprocessorConfig
        from audio_preprocessor.writer import OutputWriter

        with tempfile.TemporaryDirectory() as tmp:
            cfg = PreprocessorConfig(output_dir=tmp)
            writer = OutputWriter(cfg)

            src_wav = os.path.join(tmp, "src.wav")
            _write_wav(src_wav, _sine_wave())

            result = self._make_result(tmp)
            audio_dst, vad_dst, report_dst = writer.write(src_wav, result)

            self.assertTrue(os.path.isfile(audio_dst))
            self.assertTrue(os.path.isfile(vad_dst))
            self.assertTrue(os.path.isfile(report_dst))

    def test_vad_json_contains_speech_segments(self):
        from audio_preprocessor.config import PreprocessorConfig
        from audio_preprocessor.writer import OutputWriter

        with tempfile.TemporaryDirectory() as tmp:
            cfg = PreprocessorConfig(output_dir=tmp)
            writer = OutputWriter(cfg)
            src_wav = os.path.join(tmp, "src.wav")
            _write_wav(src_wav, _sine_wave())
            result = self._make_result(tmp)
            _, vad_dst, _ = writer.write(src_wav, result)

            with open(vad_dst) as f:
                data = json.load(f)
            self.assertIn("speech_segments", data)
            self.assertEqual(len(data["speech_segments"]), 1)

    def test_report_json_has_audio_metrics(self):
        from audio_preprocessor.config import PreprocessorConfig
        from audio_preprocessor.writer import OutputWriter

        with tempfile.TemporaryDirectory() as tmp:
            cfg = PreprocessorConfig(output_dir=tmp)
            writer = OutputWriter(cfg)
            src_wav = os.path.join(tmp, "src.wav")
            _write_wav(src_wav, _sine_wave())
            result = self._make_result(tmp)
            _, _, report_dst = writer.write(src_wav, result)

            with open(report_dst) as f:
                report = json.load(f)
            self.assertIn("audio_metrics", report)
            self.assertIn("snr_db", report["audio_metrics"])

    def test_output_dir_created_if_missing(self):
        from audio_preprocessor.config import PreprocessorConfig
        from audio_preprocessor.writer import OutputWriter

        with tempfile.TemporaryDirectory() as tmp:
            new_dir = os.path.join(tmp, "deeply", "nested", "output")
            cfg = PreprocessorConfig(output_dir=new_dir)
            writer = OutputWriter(cfg)
            src_wav = os.path.join(tmp, "src.wav")
            _write_wav(src_wav, _sine_wave())
            result = self._make_result(new_dir)
            writer.write(src_wav, result)
            self.assertTrue(os.path.isdir(new_dir))


# ============================================================ #
#  9. AudioPreprocessor – integration (all stages mocked)      #
# ============================================================ #


class TestAudioPreprocessorIntegration(unittest.TestCase):
    """
    Full-pipeline integration test with all external components mocked.

    This verifies that the orchestrator correctly wires stages together
    without requiring FFmpeg, torch, or DeepFilterNet to be installed.
    """

    def setUp(self):
        from audio_preprocessor.models import (
            AudioMetrics,
            DenoiseDecision,
            PreprocessingResult,
            VADResult,
            VADSegment,
        )

        self.metrics = AudioMetrics(
            snr_db=25.0,
            rms_db=-15.0,
            spectral_flatness=0.1,
            clipping_ratio=0.0,
            duration_seconds=180.0,
            sample_rate=16_000,
            num_channels=1,
        )
        self.decision = DenoiseDecision(
            should_denoise=False,
            strength="none",
            reason="clean audio",
            snr_at_decision=25.0,
        )
        self.vad = VADResult(
            segments=[
                VADSegment(start=15.2, end=180.5),
                VADSegment(start=183.1, end=622.7),
            ],
            total_speech_duration=604.9,
            speech_ratio=0.96,
        )

    def _run_with_mocks(self, tmp_dir: str):
        from audio_preprocessor import AudioPreprocessor, PreprocessorConfig

        cfg = PreprocessorConfig(output_dir=tmp_dir)

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("audio_preprocessor.denoiser.DeepFilterNetDenoiser._try_load_model", return_value=False), \
             patch.object(
                 __import__("audio_preprocessor.extractor", fromlist=["FFmpegExtractor"]).FFmpegExtractor,
                 "extract",
                 side_effect=lambda src, dst: _write_wav(dst, _sine_wave()),
             ), \
             patch.object(
                 __import__("audio_preprocessor.agc", fromlist=["AudioAGC"]).AudioAGC,
                 "process",
                 side_effect=lambda src, dst: __import__("shutil").copy2(src, dst),
             ), \
             patch(
                 "audio_preprocessor.quality.AudioQualityAssessor.assess",
                 return_value=self.metrics,
             ), \
             patch(
                 "audio_preprocessor.vad.SileroVAD.detect",
                 return_value=self.vad,
             ):

            ap = AudioPreprocessor(cfg)
            # Create a fake input file so _validate_input passes.
            fake_input = os.path.join(tmp_dir, "podcast.mp4")
            open(fake_input, "wb").close()
            return ap.process(fake_input)

    def test_returns_preprocessing_result(self):
        from audio_preprocessor.models import PreprocessingResult

        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_with_mocks(tmp)
            self.assertIsInstance(result, PreprocessingResult)

    def test_output_files_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_with_mocks(tmp)
            self.assertTrue(os.path.isfile(result.preprocessed_audio_path))
            self.assertTrue(os.path.isfile(result.vad_metadata_path))
            self.assertTrue(os.path.isfile(result.report_path))

    def test_vad_segments_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_with_mocks(tmp)
            self.assertEqual(len(result.vad_result.segments), 2)
            self.assertAlmostEqual(result.vad_result.segments[0].start, 15.2)
            self.assertAlmostEqual(result.vad_result.segments[1].end, 622.7)

    def test_processing_time_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_with_mocks(tmp)
            self.assertGreater(result.processing_time_seconds, 0)

    def test_file_not_found_raises(self):
        from audio_preprocessor import AudioPreprocessor, PreprocessorConfig

        with tempfile.TemporaryDirectory() as tmp:
            cfg = PreprocessorConfig(output_dir=tmp)
            with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
                 patch("audio_preprocessor.denoiser.DeepFilterNetDenoiser._try_load_model", return_value=False):
                ap = AudioPreprocessor(cfg)
                with self.assertRaises(FileNotFoundError):
                    ap.process("/nonexistent/podcast.mp4")


class TestAudioAGC(unittest.TestCase):
    def setUp(self):
        from audio_preprocessor.config import AGCConfig
        self.config = AGCConfig(enabled=True)

    def test_agc_disabled(self):
        from audio_preprocessor.agc import AudioAGC
        self.config.enabled = False
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_wav = os.path.join(tmp_dir, "input.wav")
            output_wav = os.path.join(tmp_dir, "output.wav")
            _write_wav(input_wav, _sine_wave())

            agc = AudioAGC(self.config)
            res = agc.process(input_wav, output_wav)
            self.assertEqual(res, output_wav)
            self.assertTrue(os.path.exists(output_wav))

    def test_agc_ffmpeg_not_found(self):
        from audio_preprocessor.agc import AudioAGC
        from audio_preprocessor.exceptions import AGCError
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_wav = os.path.join(tmp_dir, "input.wav")
            output_wav = os.path.join(tmp_dir, "output.wav")
            _write_wav(input_wav, _sine_wave())

            with patch("shutil.which", return_value=None):
                with self.assertRaises(AGCError):
                    AudioAGC(self.config, ffmpeg_path="invalid_ffmpeg")

    def test_agc_ffmpeg_failure(self):
        from audio_preprocessor.agc import AudioAGC
        from audio_preprocessor.exceptions import AGCError
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_wav = os.path.join(tmp_dir, "input.wav")
            output_wav = os.path.join(tmp_dir, "output.wav")
            _write_wav(input_wav, _sine_wave())

            # Mock subprocess.run to return exit code 1
            mock_res = MagicMock()
            mock_res.returncode = 1
            mock_res.stderr = "FFmpeg dynaudnorm error"
            
            with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
                 patch("subprocess.run", return_value=mock_res):
                agc = AudioAGC(self.config, ffmpeg_path="ffmpeg")
                with self.assertRaises(AGCError):
                    agc.process(input_wav, output_wav)

    def test_agc_success(self):
        from audio_preprocessor.agc import AudioAGC
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_wav = os.path.join(tmp_dir, "input.wav")
            output_wav = os.path.join(tmp_dir, "output.wav")
            _write_wav(input_wav, _sine_wave())

            mock_res = MagicMock()
            mock_res.returncode = 0
            
            def mock_run_side_effect(*args, **kwargs):
                _write_wav(output_wav, _sine_wave())
                return mock_res

            with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
                 patch("subprocess.run", side_effect=mock_run_side_effect):
                agc = AudioAGC(self.config, ffmpeg_path="ffmpeg")
                res = agc.process(input_wav, output_wav)
                self.assertEqual(res, output_wav)
                self.assertTrue(os.path.exists(output_wav))


if __name__ == "__main__":
    unittest.main(verbosity=2)
