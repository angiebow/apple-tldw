//
//  HighlightViewModel.swift
//  tldw — Transcript Content Highlighter
//

import Foundation
import Observation

@Observable
final class HighlightViewModel {
    /// The transcript produced from the dropped recording (shown as provenance in
    /// the results header). Empty until a recording has been transcribed.
    var transcriptText: String = ""
    var summary: String = ""
    var relevantLines: [LineScore] = []
    var viralLines: [LineScore] = []
    var modelInfo: ModelInfo?
    var lineCount: Int = 0
    var isLoading = false
    var statusMessage = "Start the backend, then drop in a recording to generate Shorts."
    var serverReachable: Bool?
    /// The recording this transcript came from — the editor cuts clips from it.
    var sourceVideoURL: URL?
    /// Timestamped transcript segments (with per-word times) — forwarded to the
    /// clip export so cuts can be captioned with karaoke subtitles.
    var transcriptSegments: [TranscriptSegment] = []

    private let service = HighlightService()

    var hasResults: Bool { !relevantLines.isEmpty || !viralLines.isEmpty }

    func checkHealth() async {
        serverReachable = await service.health()
    }

    /// One-shot pipeline: transcribe a dropped recording, then rank its lines.
    /// The extracted audio is the input now — there is no manual transcript step —
    /// so both stages run under a single loading state (media in → Shorts out).
    func transcribeAndHighlight(at url: URL) async {
        isLoading = true
        defer { isLoading = false }

        // ── 1. Extract audio + transcribe (backend preprocess + Whisper) ──
        statusMessage = "Extracting audio and transcribing… first run downloads the Whisper model."
        let transcript: String
        let segments: [TranscriptSegment]
        do {
            let response = try await service.transcribe(videoPath: url.path)
            let text = response.text.trimmingCharacters(in: .whitespacesAndNewlines)
            serverReachable = true
            guard !text.isEmpty else {
                clearResults()
                transcriptText = ""
                sourceVideoURL = nil
                statusMessage = "No speech found in \(response.source) — try another recording."
                return
            }
            transcript = text
            transcriptText = text
            segments = response.segments
            transcriptSegments = response.segments   // forwarded to clip export for captions
            sourceVideoURL = url   // the editor cuts clips from this recording
        } catch {
            statusMessage = error.localizedDescription
            serverReachable = false
            return
        }

        // ── 2. Summarize, embed, and score the transcribed lines ──
        // Pass segments so each ranked line keeps its start/end span (for clipping).
        statusMessage = "Summarizing, embedding, and scoring lines… first run downloads the models."
        do {
            let response = try await service.highlight(transcript, segments: segments, topK: 10)
            summary = response.summary
            relevantLines = response.relevant
            viralLines = response.viral
            modelInfo = response.models
            lineCount = response.lineCount
            statusMessage = "Done — ranked \(response.lineCount) lines."
            serverReachable = true
        } catch {
            clearResults()
            statusMessage = error.localizedDescription
            serverReachable = false
        }
    }

    /// Clear back to the empty input state (drop-a-recording screen).
    func reset() {
        transcriptText = ""
        sourceVideoURL = nil
        transcriptSegments = []
        clearResults()
        statusMessage = "Drop in a recording to generate Shorts."
    }

    /// Wipe any ranked results (used before a fresh run and on failure).
    private func clearResults() {
        summary = ""
        relevantLines = []
        viralLines = []
        modelInfo = nil
        lineCount = 0
    }
}
