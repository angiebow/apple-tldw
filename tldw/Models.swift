//
//  Models.swift
//  tldw — Phase 1: Topic Segmentation
//
//  Codable types shared with the Python BERTopic backend. JSON keys are
//  snake_case on the wire; CodingKeys map them to Swift camelCase.
//

import Foundation

/// One timestamped line of the transcript (the unit BERTopic clusters).
struct Utterance: Codable, Identifiable, Hashable {
    let start: Double
    let end: Double
    let text: String

    /// Start time is unique within a transcript, so it works as a stable id.
    var id: Double { start }
}

/// The bundled sample transcript file (`sample_transcript.json`).
struct Transcript: Codable {
    let title: String
    let utterances: [Utterance]
}

/// Request body for `POST /segment`.
struct SegmentRequest: Codable {
    let utterances: [Utterance]
    let minTopicSize: Int

    enum CodingKeys: String, CodingKey {
        case utterances
        case minTopicSize = "min_topic_size"
    }
}

/// A contiguous block of the transcript that BERTopic assigned to one topic.
struct TopicSegment: Codable, Identifiable, Hashable {
    let topicId: Int
    let label: String
    let keywords: [String]
    let start: Double
    let end: Double
    let utteranceCount: Int
    let text: String
    let isOutlier: Bool

    var id: Double { start }
    var duration: Double { end - start }

    enum CodingKeys: String, CodingKey {
        case topicId = "topic_id"
        case label
        case keywords
        case start
        case end
        case utteranceCount = "utterance_count"
        case text
        case isOutlier = "is_outlier"
    }
}

/// Response body for `POST /segment`.
struct SegmentResponse: Codable {
    let segments: [TopicSegment]
    let topicCount: Int
    let outlierUtterances: Int
    let embeddingModel: String

    enum CodingKeys: String, CodingKey {
        case segments
        case topicCount = "topic_count"
        case outlierUtterances = "outlier_utterances"
        case embeddingModel = "embedding_model"
    }
}

// MARK: - Helpers

extension Double {
    /// Format a seconds value as `m:ss` for display.
    var asTimecode: String {
        let total = Int(rounded())
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}
