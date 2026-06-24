//
//  Models.swift
//  tldw — Overall Summary (BART)
//
//  Codable wire types shared with the Python summarization backend.
//

import Foundation

/// Request body for `POST /summarize`.
struct SummarizeRequest: Codable {
    let text: String
    let maxLength: Int
    let minLength: Int
    /// Optional gold summary; when present the backend scores the generated
    /// summary against it and returns `metrics`.
    let reference: String?

    enum CodingKeys: String, CodingKey {
        case text
        case maxLength = "max_length"
        case minLength = "min_length"
        case reference
    }
}

/// Response body for `POST /summarize`.
struct SummarizeResponse: Codable {
    let summary: String
    let model: String
    let chunkCount: Int
    let inputChars: Int
    let summaryChars: Int
    /// Present only when a `reference` was supplied with the request.
    let metrics: SummaryMetrics?

    enum CodingKeys: String, CodingKey {
        case summary
        case model
        case chunkCount = "chunk_count"
        case inputChars = "input_chars"
        case summaryChars = "summary_chars"
        case metrics
    }
}

/// Summary-quality scores returned by `/evaluate` and by `/summarize` when a
/// reference is provided. Each metric measures "closeness to the reference"
/// differently: ROUGE (n-gram overlap), BERTScore (semantic), METEOR
/// (alignment with stemming + synonymy).
struct SummaryMetrics: Codable {
    let rouge: RougeScores
    let bertscore: BERTScores
    let meteor: Double
}

struct RougeScores: Codable {
    let rouge1: Double
    let rouge2: Double
    let rougeL: Double
}

struct BERTScores: Codable {
    let precision: Double
    let recall: Double
    let f1: Double
}
