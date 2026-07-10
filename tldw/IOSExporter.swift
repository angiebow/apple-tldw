//
//  IOSExporter.swift
//  tldw (iOS)
//
//  Drives clip export/merge for the iOS app: turns the user's selected Shorts
//  into ClipSpans, runs the cloud clip/merge job (with live progress), and holds
//  the downloaded result files for a share sheet. iOS-only; the macOS editor has
//  its own export path.
//

#if os(iOS)
import Foundation
import Observation
import tldwKit

@MainActor
@Observable
final class IOSExporter {
    enum Phase: Equatable { case idle, working, done, failed }

    var phase: Phase = .idle
    var progress: Double = 0
    var statusText: String = ""
    var errorMessage: String?

    /// Result files, ready to share. Merge → one URL; clips → several.
    var mergedURL: URL?
    var clipURLs: [URL] = []

    private let service = HighlightService()

    var shareItems: [URL] {
        if let mergedURL { return [mergedURL] }
        return clipURLs
    }

    func exportMerged(source: URL, lines: [LineScore], segments: [TranscriptSegment]) async {
        await run(source: source, lines: lines, segments: segments, merge: true)
    }

    func exportClips(source: URL, lines: [LineScore], segments: [TranscriptSegment]) async {
        await run(source: source, lines: lines, segments: segments, merge: false)
    }

    func reset() {
        phase = .idle
        progress = 0
        statusText = ""
        errorMessage = nil
        mergedURL = nil
        clipURLs = []
    }

    private func run(source: URL, lines: [LineScore],
                     segments: [TranscriptSegment], merge: Bool) async {
        // Only timestamped Shorts can be cut (start/end come from /transcribe).
        let spans = lines.compactMap { line -> ClipSpan? in
            guard let s = line.start, let e = line.end, e > s else { return nil }
            return ClipSpan(start: s, end: e, text: line.text)
        }
        guard !spans.isEmpty else {
            phase = .failed
            errorMessage = "The selected Shorts don't have timecodes to cut."
            return
        }

        phase = .working
        progress = 0
        errorMessage = nil
        mergedURL = nil
        clipURLs = []
        statusText = merge ? "Merging into one Short…" : "Exporting \(spans.count) clip(s)…"

        // Job progress arrives off the main actor; hop back to update UI state.
        let onProgress: (Double, String?) -> Void = { fraction, stage in
            Task { @MainActor in
                self.progress = fraction
                if let stage { self.statusText = stage }
            }
        }

        do {
            if merge {
                let response = try await service.mergeClips(
                    videoPath: source.path, clips: spans, segments: segments, progress: onProgress)
                mergedURL = URL(fileURLWithPath: response.path)
            } else {
                let response = try await service.exportClips(
                    videoPath: source.path, clips: spans, segments: segments, progress: onProgress)
                clipURLs = response.clips.map { URL(fileURLWithPath: $0.path) }
            }
            statusText = "Ready to share"
            phase = .done
        } catch {
            phase = .failed
            errorMessage = error.localizedDescription
        }
    }
}
#endif
