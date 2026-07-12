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
    var captions = true
    var backsound = false
    var backsoundVolume = 0.35
    var orientation: ClipOrientation = .portrait
    var videoDuration: Double = 1
    var thumbnails: [EditClip.ID: UIImage] = [:]

    let sourceURL: URL
    let segments: [TranscriptSegment]
    let player: AVPlayer

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
        let asset = AVURLAsset(url: sourceURL)
        if let d = try? await asset.load(.duration) { videoDuration = max(1, d.seconds) }
        await generateThumbnails(asset)
        if let clip = selected { seek(to: clip.start) }
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
        var music: [MusicPlacement]? = nil
        if backsound {
            progress(0.05, "Composing music…")
            let mood = clips.map(\.text).joined(separator: " ")
            let bed = try await service.generateBacksound(text: mood)
            music = [MusicPlacement(b64: bed.audioB64, start: 0,
                                    duration: totalDuration, volume: backsoundVolume)]
        }
        let spans = clips.map { ClipSpan(start: $0.start, end: $0.end, text: $0.text) }
        let response = try await service.mergeClips(
            videoPath: sourceURL.path, clips: spans, segments: segments,
            vertical: orientation == .portrait,
            subtitles: captions, music: music, progress: progress)
        return URL(fileURLWithPath: response.path)
    }
}
#endif
