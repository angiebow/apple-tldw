//
//  Models.swift
//  tldw — Transcript Content Highlighter
//
//  Codable wire types shared with the Python highlighter backend.
//

import Foundation

/// Request body for `POST /highlight`. When `segments` (from `/transcribe`) are
/// supplied, the backend ranks those timestamped lines so each result carries a
/// start/end span — which the editor needs to cut clips.
struct HighlightRequest: Codable {
    let text: String
    let topK: Int
    /// User-supplied video title; folded into summarization to keep the summary
    /// (and the relevance ranking) anchored to the video's topic. nil when blank.
    let title: String?
    let segments: [TranscriptSegment]?

    enum CodingKeys: String, CodingKey {
        case text, title, segments
        case topK = "top_k"
    }
}

/// One transcript line with all of its pipeline scores. The same shape is used
/// in both the "most relevant" and "most viral" lists.
struct LineScore: Codable, Identifiable {
    /// Index of the line within the parsed transcript (stable id for lists).
    let index: Int
    let text: String
    /// Cosine similarity of the line to the transcript summary (relevance).
    let relevance: Double
    /// Reranker regression output — continuous virality (0–1-ish).
    let viralScore: Double
    /// Detector P(viral) and its binary verdict.
    let viralProb: Double
    let viralLabel: Bool
    /// Span of this line in the source video (seconds). Present only when the
    /// transcript came from `/transcribe` (timestamped); nil for pasted text.
    let start: Double?
    let end: Double?

    var id: Int { index }

    enum CodingKeys: String, CodingKey {
        case index, text, relevance, start, end
        case viralScore = "viral_score"
        case viralProb = "viral_prob"
        case viralLabel = "viral_label"
    }
}

/// Request body for `POST /backsound` — generate an emotion-matched music bed.
struct BacksoundRequest: Codable {
    let text: String
    let durationS: Int?

    enum CodingKeys: String, CodingKey {
        case text
        case durationS = "duration_s"
    }
}

/// Response body for `POST /backsound`. `audioB64` is a base64-encoded WAV bed.
struct BacksoundResponse: Codable {
    /// The MusicGen prompt derived from the detected mood.
    let prompt: String
    /// Dominant emotion and its valence/arousal coordinates.
    let emotion: String
    let valence: Double
    let arousal: Double
    let audioB64: String
    let sampleRate: Int
    let durationS: Double
    let model: String

    enum CodingKeys: String, CodingKey {
        case prompt, emotion, valence, arousal, model
        case audioB64 = "audio_b64"
        case sampleRate = "sample_rate"
        case durationS = "duration_s"
    }
}

/// Request body for `POST /bloopers` — find non-speech spans in a local video.
/// Any non-speech span counts as a blooper; there is no duration threshold.
struct BlooperRequest: Codable {
    /// Absolute path to the source video on this machine (app + backend share disk).
    let videoPath: String
    let useLipCheck: Bool

    enum CodingKeys: String, CodingKey {
        case videoPath = "video_path"
        case useLipCheck = "use_lip_check"
    }
}

/// One detected non-speech span: a candidate "blooper" (silence / pause / dead air).
struct BlooperSpan: Codable, Identifiable {
    /// Position of the span in the detected list (stable id).
    let index: Int
    /// Start / end of the span in the source video, in seconds.
    let start: Double
    let end: Double
    let duration: Double
    /// Mean mouth motion over the span; nil when no face was found (`no_face`).
    let lipMotion: Double?
    /// "silent" | "lips_moving" | "no_face" — the verdict from the lip check.
    let label: String

    var id: Int { index }

    enum CodingKeys: String, CodingKey {
        case index, start, end, duration, label
        case lipMotion = "lip_motion"
    }
}

/// Response body for `POST /bloopers`.
struct BlooperResponse: Codable {
    let source: String
    /// Total duration of the source video, in seconds.
    let durationS: Double
    let count: Int
    let bloopers: [BlooperSpan]

    enum CodingKeys: String, CodingKey {
        case source, count, bloopers
        case durationS = "duration_s"
    }
}

/// Request body for `POST /transcribe` — turn a local video/audio file into text.
struct TranscribeRequest: Codable {
    /// Absolute path to the source file on this machine (app + backend share disk).
    let videoPath: String
    /// Whisper model size; nil lets the backend use its configured default.
    let model: String?

    enum CodingKeys: String, CodingKey {
        case videoPath = "video_path"
        case model
    }
}

/// One word with its timestamp — drives the karaoke (word-by-word) subtitles.
struct TranscriptWord: Codable {
    let word: String
    let start: Double
    let end: Double
}

/// One transcribed span, timestamped on the source timeline.
struct TranscriptSegment: Codable, Identifiable {
    let id: Int
    let start: Double
    let end: Double
    let text: String
    /// Per-word timings (present when the backend has word timestamps on).
    let words: [TranscriptWord]?
}

/// Response body for `POST /transcribe`.
struct TranscribeResponse: Codable {
    let source: String
    /// Total duration of the source file, in seconds.
    let durationS: Double
    /// Fraction of the audio that contained speech.
    let speechRatio: Double
    /// The Whisper model that produced the transcript.
    let model: String
    /// Full transcript (all segments joined) — this fills the highlighter input.
    let text: String
    let segments: [TranscriptSegment]

    enum CodingKeys: String, CodingKey {
        case source, model, text, segments
        case durationS = "duration_s"
        case speechRatio = "speech_ratio"
    }
}

/// One span to cut, sent in `POST /clip`.
struct ClipSpan: Codable {
    let start: Double
    let end: Double
    let text: String
    /// Optional per-clip background music bed (base64 WAV) mixed under the speech
    /// at `musicVolume` (0–1). nil when the clip has no generated bed.
    let musicB64: String?
    let musicVolume: Double?

    init(start: Double, end: Double, text: String,
         musicB64: String? = nil, musicVolume: Double? = nil) {
        self.start = start
        self.end = end
        self.text = text
        self.musicB64 = musicB64
        self.musicVolume = musicVolume
    }

    enum CodingKeys: String, CodingKey {
        case start, end, text
        case musicB64 = "music_b64"
        case musicVolume = "music_volume"
    }
}

/// Request body for `POST /clip` — cut selected spans out of the source video.
struct ClipRequest: Codable {
    let videoPath: String
    let clips: [ClipSpan]
    /// Output subfolder name; nil lets the backend use the source file's stem.
    let name: String?
    /// User-chosen destination folder; nil falls back to the backend default.
    let outputDir: String?
    /// Render 1080×1920 portrait Shorts, and burn karaoke captions (libass permitting).
    let vertical: Bool
    let subtitles: Bool
    /// Full transcript segments (with per-word times) used to caption each clip.
    let segments: [TranscriptSegment]?

    enum CodingKeys: String, CodingKey {
        case clips, name, vertical, subtitles, segments
        case videoPath = "video_path"
        case outputDir = "output_dir"
    }
}

/// One cut clip in the `/clip` response.
struct ClipInfo: Codable, Identifiable {
    let index: Int
    /// Absolute path to the written .mp4 on disk.
    let path: String
    let start: Double
    let end: Double
    let text: String

    var id: Int { index }
}

/// Response body for `POST /clip`.
struct ClipResponse: Codable {
    let source: String
    /// Folder the clips were written into.
    let outputDir: String
    let count: Int
    /// Whether the clips were rendered portrait, and whether captions were burned.
    let vertical: Bool
    let subtitlesApplied: Bool
    let subtitlesRequested: Bool
    let clips: [ClipInfo]

    enum CodingKeys: String, CodingKey {
        case source, count, clips, vertical
        case outputDir = "output_dir"
        case subtitlesApplied = "subtitles_applied"
        case subtitlesRequested = "subtitles_requested"
    }
}

/// One background-music bed placed freely on the merged timeline: it starts at
/// `start` seconds, plays for `duration` seconds (looping the WAV if shorter), at
/// `volume` under the speech. Set by the editor's draggable/cuttable Backsound lane.
struct MusicPlacement: Codable {
    let b64: String
    let start: Double
    let duration: Double
    let volume: Double
}

/// Request body for `POST /merge` — concatenate selected spans into one Short.
struct MergeRequest: Codable {
    let videoPath: String
    let clips: [ClipSpan]
    /// Output file stem; nil lets the backend use `<source>_merged`.
    let name: String?
    /// User-chosen destination folder; nil falls back to the backend default.
    let outputDir: String?
    /// Render 1080×1920 portrait, and burn karaoke captions (libass permitting).
    let vertical: Bool
    let subtitles: Bool
    /// Music beds placed on the merged timeline (draggable/cuttable). When present,
    /// per-clip `ClipSpan.musicB64` is ignored and these are mixed over the merge.
    let music: [MusicPlacement]?
    /// Full transcript segments (with per-word times) used to caption the Short.
    let segments: [TranscriptSegment]?

    enum CodingKeys: String, CodingKey {
        case clips, name, vertical, subtitles, segments, music
        case videoPath = "video_path"
        case outputDir = "output_dir"
    }
}

/// Response body for `POST /merge` — one concatenated Short written to disk.
struct MergeResponse: Codable {
    let source: String
    /// Folder the merged Short was written into.
    let outputDir: String
    /// Absolute path to the written .mp4.
    let path: String
    let clipCount: Int
    let vertical: Bool
    let subtitlesApplied: Bool
    let subtitlesRequested: Bool

    enum CodingKeys: String, CodingKey {
        case source, path, vertical
        case outputDir = "output_dir"
        case clipCount = "clip_count"
        case subtitlesApplied = "subtitles_applied"
        case subtitlesRequested = "subtitles_requested"
    }
}

/// Which models produced the response (shown as provenance in the UI).
struct ModelInfo: Codable {
    let summarizer: String
    let embedder: String
    let detector: String
    let reranker: String
}

/// Response body for `POST /highlight`.
struct HighlightResponse: Codable {
    let summary: String
    let models: ModelInfo
    let lineCount: Int
    /// Top-K lines by relevance, and top-K by virality.
    let relevant: [LineScore]
    let viral: [LineScore]

    enum CodingKeys: String, CodingKey {
        case summary, models
        case lineCount = "line_count"
        case relevant, viral
    }
}
