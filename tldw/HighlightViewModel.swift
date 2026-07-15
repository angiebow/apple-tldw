//
//  HighlightViewModel.swift
//  tldw — Transcript Content Highlighter
//

import Foundation
import Observation
import tldwKit

@Observable
final class HighlightViewModel {
    /// The transcript produced from the dropped recording (shown as provenance in
    /// the results header). Empty until a recording has been transcribed.
    var transcriptText: String = ""
    /// Optional user-supplied title for the recording; folded into summarization
    /// so the summary (and relevance ranking) stays anchored to the video's topic.
    var videoTitle: String = ""
    var summary: String = ""
    var relevantLines: [LineScore] = []
    var viralLines: [LineScore] = []
    var modelInfo: ModelInfo?
    var lineCount: Int = 0
    var isLoading = false
    /// Which pipeline phase the loading screen is on: 0 = transcribing (extract →
    /// clean → Whisper), 1 = ranking (summarize → embed → score), 2 = done.
    /// The emoji step-list on the loading screen reads this to mark stages
    /// done / active / pending.
    var loadingStage = 0
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
        loadingStage = 0
        defer { isLoading = false }

#if os(iOS)
        // iOS ships fully self-contained: transcribe on the free HF Whisper API,
        // rank on-device with the trained Core ML models (no backend to host).

        // ── 1. Extract audio on-device + transcribe on the free HF Whisper API ──
        statusMessage = "Extracting the audio and transcribing it on Hugging Face. Whisper may take a moment to warm up on the first run."
        let segments: [TranscriptSegment]
        do {
            let result = try await HFTranscriber.transcribe(videoURL: url)
            let text = result.text.trimmingCharacters(in: .whitespacesAndNewlines)
            serverReachable = true
            guard !text.isEmpty else {
                clearResults()
                transcriptText = ""
                sourceVideoURL = nil
                statusMessage = "No speech found in that recording — try another."
                return
            }
            transcriptText = text
            segments = result.segments
            transcriptSegments = result.segments   // forwarded to clip export for captions
            sourceVideoURL = url   // the editor cuts clips from this recording
        } catch {
            statusMessage = error.localizedDescription
            serverReachable = false
            return
        }

        // ── 2. Score every scene on-device with the trained Core ML models ──
        loadingStage = 1
        statusMessage = "Scoring each moment for relevance and viral potential — all on your device."
        do {
            let result = try await Task.detached(priority: .userInitiated) {
                try LocalHighlighter.shared.rank(segments: segments, topK: 10)
            }.value
            summary = ""   // no LLM summary on-device
            relevantLines = result.relevant
            viralLines = result.viral
            modelInfo = nil
            lineCount = result.lineCount
            loadingStage = 2
            statusMessage = "Done — ranked \(result.lineCount) moments."
        } catch {
            clearResults()
            statusMessage = error.localizedDescription
        }
#else
        // macOS uses the local backend (the desktop app runs it on the same Mac).

        // ── 1. Extract audio + transcribe (backend preprocess + Whisper) ──
        statusMessage = "Pulling the audio out, cleaning it up, and transcribing every word. The first run downloads the Whisper model, so give it a minute."
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
            transcriptSegments = response.segments
            sourceVideoURL = url
        } catch {
            statusMessage = error.localizedDescription
            serverReachable = false
            return
        }

        // ── 2. Summarize, embed, and score the transcribed lines ──
        loadingStage = 1
        statusMessage = "Summarizing what was said, then scoring every line for relevance and viral potential. The first run downloads the ranking models."
        do {
            let title = videoTitle.trimmingCharacters(in: .whitespacesAndNewlines)
            let response = try await service.highlight(transcript,
                                                       title: title.isEmpty ? nil : title,
                                                       segments: segments, topK: 10)
            summary = response.summary
            relevantLines = response.relevant
            viralLines = response.viral
            modelInfo = response.models
            lineCount = response.lineCount
            loadingStage = 2
            statusMessage = "Done — ranked \(response.lineCount) lines."
            serverReachable = true
        } catch {
            clearResults()
            statusMessage = error.localizedDescription
            serverReachable = false
        }
#endif
    }

    /// Clear back to the empty input state (drop-a-recording screen).
    func reset() {
        transcriptText = ""
        videoTitle = ""
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
