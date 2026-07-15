//
//  IOSEditorModel.swift
//  tldw (iOS)
//
//  State + logic for the merged-timeline editor: the selected Shorts become
//  reorderable/trimmable clips on one timeline, previewed with an AVPlayer, then
//  exported as a single merged Short (optionally with burned captions and a
//  backsound music bed). Tools map to real backend features (no transitions).
//

#if os(iOS)
import AVFoundation
import Foundation
import Observation
import UIKit
import tldwKit

struct EditClip: Identifiable, Equatable {
    let id = UUID()
    var start: Double
    var end: Double
    var text: String
    var duration: Double { max(0.1, end - start) }
}

enum ClipOrientation: String, CaseIterable, Identifiable {
    case portrait = "Portrait", landscape = "Landscape"
    var id: String { rawValue }
    /// Preview aspect ratio (width / height).
    var aspect: CGFloat { self == .portrait ? 9.0 / 16.0 : 16.0 / 9.0 }
    var ratioLabel: String { self == .portrait ? "9:16" : "16:9" }
    var icon: String { self == .portrait ? "rectangle.portrait" : "rectangle" }
}

@MainActor
@Observable
final class IOSEditorModel: Identifiable {
    nonisolated let id = UUID()
    var clips: [EditClip]
    var selectedID: EditClip.ID?
    var captions = false     // caption burning disabled for now
    var backsound = false
    var backsoundVolume = 0.35
    var orientation: ClipOrientation = .portrait
    var features = BackendFeatures()
    var videoDuration: Double = 1
    var thumbnails: [EditClip.ID: UIImage] = [:]

    let sourceURL: URL
    let segments: [TranscriptSegment]
    let player: AVPlayer
    private var sourceTransform: CGAffineTransform = .identity

    private let service = HighlightService()
    private var endObserver: Any?

    init(sourceURL: URL, lines: [LineScore], segments: [TranscriptSegment]) {
        self.sourceURL = sourceURL
        self.segments = segments
        self.clips = lines.compactMap { line in
            guard let s = line.start, let e = line.end, e > s else { return nil }
            return EditClip(start: s, end: e, text: line.text)
        }
        self.player = AVPlayer(url: sourceURL)
        self.selectedID = clips.first?.id
    }

    var selected: EditClip? { clips.first { $0.id == selectedID } }
    var totalDuration: Double { clips.reduce(0) { $0 + $1.duration } }

    // MARK: load (duration + thumbnails)

    func load() async {
        features = await service.capabilities()
        let asset = AVURLAsset(url: sourceURL)
        if let d = try? await asset.load(.duration) { videoDuration = max(1, d.seconds) }
        sourceTransform = (try? await asset.loadTracks(withMediaType: .video).first?
            .load(.preferredTransform)) ?? .identity
        await applyPreviewComposition()
        await generateThumbnails(asset)
        if let clip = selected { seek(to: clip.start) }
    }

    /// Switch orientation and update the live preview so it shows the same 9:16
    /// blurred-background framing the export will produce (WYSIWYG).
    func setOrientation(_ new: ClipOrientation) {
        guard new != orientation else { return }
        orientation = new
        Task { await applyPreviewComposition() }
    }

    private func applyPreviewComposition() async {
        guard let item = player.currentItem else { return }
        if orientation == .portrait {
            item.videoComposition = LocalVideoExporter.blurredPortraitComposition(
                for: item.asset, preferred: sourceTransform)
        } else {
            item.videoComposition = nil
        }
        // Nudge the (possibly paused) player so it redraws the current frame
        // through the new composition immediately.
        seek(to: player.currentTime().seconds)
    }

    private func generateThumbnails(_ asset: AVURLAsset) async {
        let gen = AVAssetImageGenerator(asset: asset)
        gen.appliesPreferredTrackTransform = true
        gen.maximumSize = CGSize(width: 160, height: 280)
        gen.requestedTimeToleranceBefore = .positiveInfinity
        gen.requestedTimeToleranceAfter = .positiveInfinity
        for clip in clips {
            let time = CMTime(seconds: clip.start + 0.05, preferredTimescale: 600)
            if let cg = try? await image(gen, at: time) {
                thumbnails[clip.id] = UIImage(cgImage: cg)
            }
        }
    }

    private func image(_ gen: AVAssetImageGenerator, at time: CMTime) async throws -> CGImage {
        try await withCheckedThrowingContinuation { cont in
            gen.generateCGImageAsynchronously(for: time) { cg, _, error in
                if let cg { cont.resume(returning: cg) }
                else { cont.resume(throwing: error ?? URLError(.unknown)) }
            }
        }
    }

    // MARK: preview

    func select(_ clip: EditClip) {
        selectedID = clip.id
        seek(to: clip.start)
    }

    private func seek(to seconds: Double) {
        player.seek(to: CMTime(seconds: seconds, preferredTimescale: 600),
                    toleranceBefore: .zero, toleranceAfter: .zero)
    }

    func playSelected() {
        guard let clip = selected else { return }
        seek(to: clip.start)
        if let endObserver { player.removeTimeObserver(endObserver) }
        let end = NSValue(time: CMTime(seconds: clip.end, preferredTimescale: 600))
        endObserver = player.addBoundaryTimeObserver(forTimes: [end], queue: .main) { [weak self] in
            self?.player.pause()
        }
        player.play()
    }

    func pause() { player.pause() }

    // MARK: order / trim

    private func index(of id: EditClip.ID?) -> Int? {
        guard let id else { return nil }
        return clips.firstIndex { $0.id == id }
    }

    func moveLeft(_ clip: EditClip) {
        guard let i = index(of: clip.id), i > 0 else { return }
        clips.swapAt(i, i - 1)
    }

    func moveRight(_ clip: EditClip) {
        guard let i = index(of: clip.id), i < clips.count - 1 else { return }
        clips.swapAt(i, i + 1)
    }

    /// Move the clip at `from` to index `to` (interactive drag reorder).
    func move(from: Int, to: Int) {
        guard clips.indices.contains(from) else { return }
        let dest = max(0, min(to, clips.count - 1))
        guard dest != from else { return }
        let clip = clips.remove(at: from)
        clips.insert(clip, at: dest)
    }

    func delete(_ clip: EditClip) {
        clips.removeAll { $0.id == clip.id }
        if selectedID == clip.id { selectedID = clips.first?.id }
    }

    func setTrim(start: Double, end: Double) {
        guard let i = index(of: selectedID) else { return }
        clips[i].start = max(0, min(start, end - 0.1))
        clips[i].end = min(videoDuration, max(end, start + 0.1))
        seek(to: clips[i].start)
    }

    // MARK: export

    func export(progress: @escaping (Double, String?) -> Void) async throws -> URL {
        // Video assembly runs on-device (AVFoundation) — the cut/merge/caption
        // step can't be hosted free, so it lives here and costs nothing.
        let spans = clips.map { LocalVideoExporter.Span(start: $0.start, end: $0.end, text: $0.text) }
        return try await LocalVideoExporter.merge(
            source: sourceURL, spans: spans,
            vertical: orientation == .portrait,
            subtitles: captions, progress: progress)
    }
}
#endif
