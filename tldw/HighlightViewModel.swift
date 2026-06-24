//
//  HighlightViewModel.swift
//  tldw — Transcript Content Highlighter
//

import Foundation
import Observation

@Observable
final class HighlightViewModel {
    var transcriptText: String = SampleText.podcastExcerpt
    var summary: String = ""
    var relevantLines: [LineScore] = []
    var viralLines: [LineScore] = []
    var modelInfo: ModelInfo?
    var lineCount: Int = 0
    var isLoading = false
    var statusMessage = "Paste a transcript (or use the sample), start the backend, then tap Find Highlights."
    var serverReachable: Bool?

    private let service = HighlightService()

    var hasResults: Bool { !relevantLines.isEmpty || !viralLines.isEmpty }

    func checkHealth() async {
        serverReachable = await service.health()
    }

    func findHighlights() async {
        let text = transcriptText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.count >= 40 else {
            statusMessage = "Add a bit more text — at least ~40 characters."
            return
        }

        isLoading = true
        statusMessage = "Summarizing, embedding, and scoring lines… first run downloads the models."
        defer { isLoading = false }

        do {
            let response = try await service.highlight(text, topK: 10)
            summary = response.summary
            relevantLines = response.relevant
            viralLines = response.viral
            modelInfo = response.models
            lineCount = response.lineCount
            statusMessage = "Done — ranked \(response.lineCount) lines."
            serverReachable = true
        } catch {
            summary = ""
            relevantLines = []
            viralLines = []
            modelInfo = nil
            lineCount = 0
            statusMessage = error.localizedDescription
            serverReachable = false
        }
    }

    func loadSample() {
        transcriptText = SampleText.podcastExcerpt
        summary = ""
        relevantLines = []
        viralLines = []
        modelInfo = nil
        lineCount = 0
        statusMessage = "Sample loaded."
    }
}
