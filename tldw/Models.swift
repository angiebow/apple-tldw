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
    let segments: [TranscriptSegment]?

    enum CodingKeys: String, CodingKey {
        case text, segments
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

/// One transcribed span, timestamped on the source timeline.
struct TranscriptSegment: Codable, Identifiable {
    let id: Int
    let start: Double
    let end: Double
    let text: String
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
}

/// Request body for `POST /clip` — cut selected spans out of the source video.
struct ClipRequest: Codable {
    let videoPath: String
    let clips: [ClipSpan]
    /// Output subfolder name; nil lets the backend use the source file's stem.
    let name: String?

    enum CodingKeys: String, CodingKey {
        case clips, name
        case videoPath = "video_path"
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
    let clips: [ClipInfo]

    enum CodingKeys: String, CodingKey {
        case source, count, clips
        case outputDir = "output_dir"
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
