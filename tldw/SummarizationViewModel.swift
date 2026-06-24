//
//  SummarizationViewModel.swift
//  tldw — Overall Summary (BART)
//

import Foundation
import Observation

@Observable
final class SummarizationViewModel {
    var inputText: String = SampleText.podcastExcerpt
    var referenceText: String = ""
    var summary: String = ""
    var stats: String = ""
    var metrics: SummaryMetrics?
    var isLoading = false
    var statusMessage = "Paste text (or use the sample), start the backend, then tap Summarize."
    var serverReachable: Bool?

    private let service = SummarizationService()

    func checkHealth() async {
        serverReachable = await service.health()
    }

    func summarize() async {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.count >= 40 else {
            statusMessage = "Add a bit more text — at least ~40 characters."
            return
        }
        let reference = referenceText.trimmingCharacters(in: .whitespacesAndNewlines)
        let hasReference = !reference.isEmpty

        isLoading = true
        statusMessage = hasReference
            ? "Summarizing with BART, then scoring against your reference…"
            : "Summarizing with BART… first run downloads the model (~1.6GB)."
        metrics = nil
        defer { isLoading = false }

        do {
            let response = try await service.summarize(text, reference: hasReference ? reference : nil)
            summary = response.summary
            stats = "\(response.model) · \(response.chunkCount) chunk(s) · "
                + "\(response.inputChars) → \(response.summaryChars) chars"
            metrics = response.metrics
            statusMessage = hasReference ? "Done — summary scored against the reference." : "Done."
            serverReachable = true
        } catch {
            summary = ""
            stats = ""
            metrics = nil
            statusMessage = error.localizedDescription
            serverReachable = false
        }
    }

    func loadSample() {
        inputText = SampleText.podcastExcerpt
        summary = ""
        stats = ""
        metrics = nil
        statusMessage = "Sample loaded."
    }
}
