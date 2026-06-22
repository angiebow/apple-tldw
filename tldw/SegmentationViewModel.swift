//
//  SegmentationViewModel.swift
//  tldw — Phase 1: Topic Segmentation
//

import Foundation
import Observation
import SwiftUI

@Observable
final class SegmentationViewModel {
    var transcript: Transcript
    var segments: [TopicSegment] = []
    var isLoading = false
    var statusMessage = "Loaded sample. Start the backend, then tap Segment."
    var serverReachable: Bool?

    private let service = SegmentationService()

    init() {
        transcript = SampleLoader.load()
    }

    func checkHealth() async {
        serverReachable = await service.health()
    }

    func runSegmentation() async {
        guard !transcript.utterances.isEmpty else {
            statusMessage = "No transcript loaded."
            return
        }
        isLoading = true
        statusMessage = "Running BERTopic… first run downloads the embedding model (~30–60s)."
        defer { isLoading = false }

        do {
            let response = try await service.segment(transcript.utterances)
            segments = response.segments
            statusMessage = "\(response.topicCount) topics · \(response.segments.count) segments · "
                + "\(response.outlierUtterances) outlier utterances · \(response.embeddingModel)"
            serverReachable = true
        } catch {
            segments = []
            statusMessage = error.localizedDescription
            serverReachable = false
        }
    }

    /// Stable color per topic id so segments read as a colored timeline.
    func color(for segment: TopicSegment) -> Color {
        guard !segment.isOutlier else { return .gray }
        let palette: [Color] = [.blue, .green, .orange, .purple, .pink, .teal, .indigo, .red]
        return palette[((segment.topicId % palette.count) + palette.count) % palette.count]
    }
}

/// Loads the sample transcript bundled with the app.
enum SampleLoader {
    static func load() -> Transcript {
        guard let url = Bundle.main.url(forResource: "sample_transcript", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let transcript = try? JSONDecoder().decode(Transcript.self, from: data) else {
            return Transcript(title: "No sample found", utterances: [])
        }
        return transcript
    }
}
