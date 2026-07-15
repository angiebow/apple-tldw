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
public struct HighlightRequest: Codable {
    public let text: String
    public let topK: Int
    /// User-supplied video title; folded into summarization to keep the summary
    /// (and the relevance ranking) anchored to the video's topic. nil when blank.
    public let title: String?
    public let segments: [TranscriptSegment]?

    enum CodingKeys: String, CodingKey {
        case text, title, segments
        case topK = "top_k"
    }
}

/// One transcript line with all of its pipeline scores. The same shape is used
/// in both the "most relevant" and "most viral" lists.
public struct LineScore: Codable, Identifiable {
    /// Index of the line within the parsed transcript (stable id for lists).
    public let index: Int
    public let text: String
    /// Cosine similarity of the line to the transcript summary (relevance).
    public let relevance: Double
    /// Reranker regression output — continuous virality (0–1-ish).
    public let viralScore: Double
    /// Detector P(viral) and its binary verdict.
    public let viralProb: Double
    public let viralLabel: Bool
    /// Span of this line in the source video (seconds). Present only when the
    /// transcript came from `/transcribe` (timestamped); nil for pasted text.
    public let start: Double?
    public let end: Double?

    public var id: Int { index }

    public init(index: Int, text: String, relevance: Double, viralScore: Double,
                viralProb: Double, viralLabel: Bool, start: Double? = nil, end: Double? = nil) {
        self.index = index
        self.text = text
        self.relevance = relevance
        self.viralScore = viralScore
        self.viralProb = viralProb
        self.viralLabel = viralLabel
        self.start = start
        self.end = end
    }

    enum CodingKeys: String, CodingKey {
        case index, text, relevance, start, end
        case viralScore = "viral_score"
        case viralProb = "viral_prob"
        case viralLabel = "viral_label"
    }
}

/// Request body for `POST /backsound` — generate an emotion-matched music bed.
public struct BacksoundRequest: Codable {
    public let text: String
    public let durationS: Int?

    enum CodingKeys: String, CodingKey {
        case text
        case durationS = "duration_s"
    }
}

/// Response body for `POST /backsound`. `audioB64` is a base64-encoded WAV bed.
public struct BacksoundResponse: Codable {
    /// The MusicGen prompt derived from the detected mood.
    public let prompt: String
    /// Dominant emotion and its valence/arousal coordinates.
    public let emotion: String
    public let valence: Double
    public let arousal: Double
    public let audioB64: String
    public let sampleRate: Int
    public let durationS: Double
    public let model: String

    enum CodingKeys: String, CodingKey {
        case prompt, emotion, valence, arousal, model
        case audioB64 = "audio_b64"
        case sampleRate = "sample_rate"
        case durationS = "duration_s"
    }
}

/// Request body for `POST /bloopers` — find non-speech spans in a local video.
/// Any non-speech span counts as a blooper; there is no duration threshold.
public struct BlooperRequest: Codable {
    /// Absolute path to the source video on this machine (app + backend share disk).
    public let videoPath: String
    public let useLipCheck: Bool

    enum CodingKeys: String, CodingKey {
        case videoPath = "video_path"
        case useLipCheck = "use_lip_check"
    }
}

/// One detected non-speech span: a candidate "blooper" (silence / pause / dead air).
public struct BlooperSpan: Codable, Identifiable {
    /// Position of the span in the detected list (stable id).
    public let index: Int
    /// Start / end of the span in the source video, in seconds.
    public let start: Double
    public let end: Double
    public let duration: Double
    /// Mean mouth motion over the span; nil when no face was found (`no_face`).
    public let lipMotion: Double?
    /// "silent" | "lips_moving" | "no_face" — the verdict from the lip check.
    public let label: String

    public var id: Int { index }

    enum CodingKeys: String, CodingKey {
        case index, start, end, duration, label
        case lipMotion = "lip_motion"
    }
}

/// Response body for `POST /bloopers`.
public struct BlooperResponse: Codable {
    public let source: String
    /// Total duration of the source video, in seconds.
    public let durationS: Double
    public let count: Int
    public let bloopers: [BlooperSpan]

    enum CodingKeys: String, CodingKey {
        case source, count, bloopers
        case durationS = "duration_s"
    }
}

/// Request body for `POST /transcribe` — turn a local video/audio file into text.
public struct TranscribeRequest: Codable {
    /// Absolute path to the source file on this machine (app + backend share disk).
    public let videoPath: String
    /// Whisper model size; nil lets the backend use its configured default.
    public let model: String?

    enum CodingKeys: String, CodingKey {
        case videoPath = "video_path"
        case model
    }
}

/// One word with its timestamp — drives the karaoke (word-by-word) subtitles.
public struct TranscriptWord: Codable {
    public let word: String
    public let start: Double
    public let end: Double

    public init(word: String, start: Double, end: Double) {
        self.word = word
        self.start = start
        self.end = end
    }
}

/// One transcribed span, timestamped on the source timeline.
public struct TranscriptSegment: Codable, Identifiable {
    public let id: Int
    public let start: Double
    public let end: Double
    public let text: String
    /// Per-word timings (present when the backend has word timestamps on).
    public let words: [TranscriptWord]?

    public init(id: Int, start: Double, end: Double, text: String, words: [TranscriptWord]? = nil) {
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.words = words
    }
}

/// Response body for `POST /transcribe`.
public struct TranscribeResponse: Codable {
    public let source: String
    /// Total duration of the source file, in seconds.
    public let durationS: Double
    /// Fraction of the audio that contained speech.
    public let speechRatio: Double
    /// The Whisper model that produced the transcript.
    public let model: String
    /// Full transcript (all segments joined) — this fills the highlighter input.
    public let text: String
    public let segments: [TranscriptSegment]

    enum CodingKeys: String, CodingKey {
        case source, model, text, segments
        case durationS = "duration_s"
        case speechRatio = "speech_ratio"
    }
}

/// One span to cut, sent in `POST /clip`.
public struct ClipSpan: Codable {
    public let start: Double
    public let end: Double
    public let text: String
    /// Optional per-clip background music bed (base64 WAV) mixed under the speech
    /// at `musicVolume` (0–1). nil when the clip has no generated bed.
    public let musicB64: String?
    public let musicVolume: Double?

    public init(start: Double, end: Double, text: String,
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
public struct ClipRequest: Codable {
    public let videoPath: String
    public let clips: [ClipSpan]
    /// Output subfolder name; nil lets the backend use the source file's stem.
    public let name: String?
    /// User-chosen destination folder; nil falls back to the backend default.
    public let outputDir: String?
    /// Render 1080×1920 portrait Shorts, and burn karaoke captions (libass permitting).
    public let vertical: Bool
    public let subtitles: Bool
    /// Full transcript segments (with per-word times) used to caption each clip.
    public let segments: [TranscriptSegment]?

    enum CodingKeys: String, CodingKey {
        case clips, name, vertical, subtitles, segments
        case videoPath = "video_path"
        case outputDir = "output_dir"
    }
}

/// One cut clip in the `/clip` response.
public struct ClipInfo: Codable, Identifiable {
    public let index: Int
    /// Absolute path to the written .mp4 on disk.
    public let path: String
    public let start: Double
    public let end: Double
    public let text: String

    public var id: Int { index }
}

/// Response body for `POST /clip`.
public struct ClipResponse: Codable {
    public let source: String
    /// Folder the clips were written into.
    public let outputDir: String
    public let count: Int
    /// Whether the clips were rendered portrait, and whether captions were burned.
    public let vertical: Bool
    public let subtitlesApplied: Bool
    public let subtitlesRequested: Bool
    public let clips: [ClipInfo]

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
public struct MusicPlacement: Codable {
    public let b64: String
    public let start: Double
    public let duration: Double
    public let volume: Double

    public init(b64: String, start: Double, duration: Double, volume: Double) {
        self.b64 = b64
        self.start = start
        self.duration = duration
        self.volume = volume
    }
}

/// Request body for `POST /merge` — concatenate selected spans into one Short.
public struct MergeRequest: Codable {
    public let videoPath: String
    public let clips: [ClipSpan]
    /// Output file stem; nil lets the backend use `<source>_merged`.
    public let name: String?
    /// User-chosen destination folder; nil falls back to the backend default.
    public let outputDir: String?
    /// Render 1080×1920 portrait, and burn karaoke captions (libass permitting).
    public let vertical: Bool
    public let subtitles: Bool
    /// Music beds placed on the merged timeline (draggable/cuttable). When present,
    /// per-clip `ClipSpan.musicB64` is ignored and these are mixed over the merge.
    public let music: [MusicPlacement]?
    /// Full transcript segments (with per-word times) used to caption the Short.
    public let segments: [TranscriptSegment]?

    enum CodingKeys: String, CodingKey {
        case clips, name, vertical, subtitles, segments, music
        case videoPath = "video_path"
        case outputDir = "output_dir"
    }
}

/// Response body for `POST /merge` — one concatenated Short written to disk.
public struct MergeResponse: Codable {
    public let source: String
    /// Folder the merged Short was written into.
    public let outputDir: String
    /// Absolute path to the written .mp4.
    public let path: String
    public let clipCount: Int
    public let vertical: Bool
    public let subtitlesApplied: Bool
    public let subtitlesRequested: Bool

    enum CodingKeys: String, CodingKey {
        case source, path, vertical
        case outputDir = "output_dir"
        case clipCount = "clip_count"
        case subtitlesApplied = "subtitles_applied"
        case subtitlesRequested = "subtitles_requested"
    }
}

/// Which models produced the response (shown as provenance in the UI).
public struct ModelInfo: Codable {
    public let summarizer: String
    public let embedder: String
    public let detector: String
    public let reranker: String
}

/// Response body for `POST /highlight`.
public struct HighlightResponse: Codable {
    public let summary: String
    public let models: ModelInfo
    public let lineCount: Int
    /// Top-K lines by relevance, and top-K by virality.
    public let relevant: [LineScore]
    public let viral: [LineScore]

    enum CodingKeys: String, CodingKey {
        case summary, models
        case lineCount = "line_count"
        case relevant, viral
    }
}
