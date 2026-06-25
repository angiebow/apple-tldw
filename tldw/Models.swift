//
//  Models.swift
//  tldw — Transcript Content Highlighter
//
//  Codable wire types shared with the Python highlighter backend.
//

import Foundation

/// Request body for `POST /highlight`.
struct HighlightRequest: Codable {
    let text: String
    let topK: Int

    enum CodingKeys: String, CodingKey {
        case text
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

    var id: Int { index }

    enum CodingKeys: String, CodingKey {
        case index, text, relevance
        case viralScore = "viral_score"
        case viralProb = "viral_prob"
        case viralLabel = "viral_label"
    }
}

/// Request body for `POST /sfx` — generate a sound effect for one selected line.
struct SFXRequest: Codable {
    let text: String
    let viralScore: Double?
    let durationS: Int?

    enum CodingKeys: String, CodingKey {
        case text
        case viralScore = "viral_score"
        case durationS = "duration_s"
    }
}

/// Response body for `POST /sfx`. `audioB64` is a base64-encoded WAV clip.
struct SFXResponse: Codable {
    /// The text-to-audio prompt the backend derived from the line.
    let prompt: String
    let audioB64: String
    let sampleRate: Int
    let durationS: Double
    /// Which audio model produced it (a stub until Stable Audio is wired up).
    let model: String

    enum CodingKeys: String, CodingKey {
        case prompt, model
        case audioB64 = "audio_b64"
        case sampleRate = "sample_rate"
        case durationS = "duration_s"
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
