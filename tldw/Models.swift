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
