//
//  LocalVideoExporter.swift
//  tldw (iOS)
//
//  On-device video assembly with AVFoundation — the steps a free Hugging Face
//  Space can't host (ffmpeg cut / merge / caption burn are code, not a model).
//  Transcription + ranking still come from the backend; this turns the chosen
//  spans into finished Shorts locally, so producing the video costs nothing and
//  scales per-device.
//
//  Does: trim spans from the source, concatenate them end to end, reframe to
//  9:16 (aspect-fill center-crop) or keep landscape, and burn one caption per
//  clip (each highlight clip is a sentence, so no fragile word-level sync).
//

#if os(iOS)
import AVFoundation
import CoreImage
import CoreMedia
import Foundation
import UIKit
import tldwKit

enum LocalVideoError: LocalizedError {
    case noVideoTrack
    case exportFailed(String)
    case emptySelection

    var errorDescription: String? {
        switch self {
        case .noVideoTrack:        return "That recording has no video track."
        case .emptySelection:      return "No clips to export."
        case .exportFailed(let m): return "Couldn't render the video (\(m))."
        }
    }
}

struct LocalVideoExporter {
    struct Span {
        var start: Double
        var end: Double
        var text: String
        var duration: Double { max(0.05, end - start) }
    }

    /// Merge the spans into a single Short.
    static func merge(source: URL, spans: [Span], vertical: Bool, subtitles: Bool,
                      progress: ((Double, String?) -> Void)? = nil) async throws -> URL {
        guard !spans.isEmpty else { throw LocalVideoError.emptySelection }
        progress?(0.1, "Assembling clips…")
        let (composition, videoComp) = try await build(source: source, spans: spans,
                                                        vertical: vertical, subtitles: subtitles)
        progress?(0.35, "Rendering…")
        return try await export(composition, videoComp: videoComp,
                                name: "vireel-short", progress: progress)
    }

    /// Export each span as its own separate Short file.
    static func clips(source: URL, spans: [Span], vertical: Bool, subtitles: Bool,
                      progress: ((Double, String?) -> Void)? = nil) async throws -> [URL] {
        guard !spans.isEmpty else { throw LocalVideoError.emptySelection }
        var urls: [URL] = []
        for (i, span) in spans.enumerated() {
            progress?(Double(i) / Double(spans.count), "Rendering clip \(i + 1) of \(spans.count)…")
            let (composition, videoComp) = try await build(source: source, spans: [span],
                                                           vertical: vertical, subtitles: subtitles)
            let url = try await export(composition, videoComp: videoComp,
                                       name: "vireel-clip-\(i + 1)", progress: nil)
            urls.append(url)
        }
        progress?(1, "Done")
        return urls
    }

    // MARK: - Composition

    private static func build(source: URL, spans: [Span], vertical: Bool, subtitles: Bool)
        async throws -> (AVMutableComposition, AVMutableVideoComposition) {
        let asset = AVURLAsset(url: source)
        guard let srcVideo = try await asset.loadTracks(withMediaType: .video).first else {
            throw LocalVideoError.noVideoTrack
        }
        let srcAudio = try await asset.loadTracks(withMediaType: .audio).first

        let composition = AVMutableComposition()
        let vTrack = composition.addMutableTrack(withMediaType: .video,
                                                 preferredTrackID: kCMPersistentTrackID_Invalid)!
        let aTrack = srcAudio == nil ? nil :
            composition.addMutableTrack(withMediaType: .audio,
                                        preferredTrackID: kCMPersistentTrackID_Invalid)
        let assetDuration = try await asset.load(.duration)

        // Insert each span end-to-end; remember where each lands for captions.
        var cursor = CMTime.zero
        var placed: [(range: CMTimeRange, text: String)] = []
        for span in spans {
            let start = CMTime(seconds: max(0, span.start), preferredTimescale: 600)
            let end = CMTime(seconds: span.end, preferredTimescale: 600)
            let clamped = CMTimeRange(start: start,
                                      duration: CMTimeMinimum(end - start, assetDuration - start))
            guard clamped.duration.seconds > 0.05 else { continue }
            try vTrack.insertTimeRange(clamped, of: srcVideo, at: cursor)
            if let aTrack, let srcAudio {
                try? aTrack.insertTimeRange(clamped, of: srcAudio, at: cursor)
            }
            placed.append((CMTimeRange(start: cursor, duration: clamped.duration), span.text))
            cursor = cursor + clamped.duration
        }
        guard cursor.seconds > 0 else { throw LocalVideoError.emptySelection }

        // Reframe transform.
        let natural = try await srcVideo.load(.naturalSize)
        let preferred = try await srcVideo.load(.preferredTransform)
        let displayed = natural.applying(preferred)
        let srcSize = CGSize(width: abs(displayed.width), height: abs(displayed.height))
        let renderSize = vertical ? CGSize(width: 1080, height: 1920)
                                  : CGSize(width: 1920, height: 1080)

        // Portrait: fit the clip to the width and fill the top/bottom with a
        // blurred, scaled-up copy of the same frame (the classic Shorts look) —
        // done per frame with Core Image, so nothing on the sides is cropped.
        if vertical {
            return (composition, blurredPortraitComposition(for: composition, preferred: preferred))
        }

        let scale = max(renderSize.width / srcSize.width, renderSize.height / srcSize.height)
        let scaledW = srcSize.width * scale, scaledH = srcSize.height * scale
        let tx = (renderSize.width - scaledW) / 2, ty = (renderSize.height - scaledH) / 2
        let transform = preferred
            .concatenating(CGAffineTransform(scaleX: scale, y: scale))
            .concatenating(CGAffineTransform(translationX: tx, y: ty))

        let layerInstruction = AVMutableVideoCompositionLayerInstruction(assetTrack: vTrack)
        layerInstruction.setTransform(transform, at: .zero)
        let instruction = AVMutableVideoCompositionInstruction()
        instruction.timeRange = CMTimeRange(start: .zero, duration: cursor)
        instruction.layerInstructions = [layerInstruction]

        let videoComp = AVMutableVideoComposition()
        videoComp.instructions = [instruction]
        videoComp.frameDuration = CMTime(value: 1, timescale: 30)
        videoComp.renderSize = renderSize

        if subtitles {
            addCaptions(to: videoComp, placed: placed, renderSize: renderSize, total: cursor)
        }
        return (composition, videoComp)
    }

    // MARK: - Portrait blurred-background compositing

    private static let ciContext = CIContext()

    /// A 9:16 blurred-background video composition for `asset` — used for both the
    /// final export and the live editor preview, so what you see matches what ships.
    static func blurredPortraitComposition(for asset: AVAsset,
                                           preferred: CGAffineTransform) -> AVMutableVideoComposition {
        let render = CGSize(width: 1080, height: 1920)
        let rect = CGRect(origin: .zero, size: render)
        let comp = AVMutableVideoComposition(asset: asset) { request in
            let out = blurredPortrait(request.sourceImage, preferred: preferred,
                                      render: render, rect: rect)
            request.finish(with: out, context: ciContext)
        }
        comp.renderSize = render
        comp.frameDuration = CMTime(value: 1, timescale: 30)
        return comp
    }

    /// Render one frame into 9:16: the clip fit to the full width and centered,
    /// over a blurred, aspect-filled copy of the same frame filling top & bottom.
    private static func blurredPortrait(_ source: CIImage, preferred: CGAffineTransform,
                                        render: CGSize, rect: CGRect) -> CIImage {
        // Orient the raw frame upright and normalize its origin to (0,0).
        var img = source.transformed(by: preferred)
        img = img.transformed(by: CGAffineTransform(translationX: -img.extent.origin.x,
                                                    y: -img.extent.origin.y))
        let sw = img.extent.width, sh = img.extent.height
        guard sw > 0, sh > 0 else { return source }

        // Background: aspect-fill the frame, then blur.
        let bgScale = max(render.width / sw, render.height / sh)
        var bg = img.transformed(by: CGAffineTransform(scaleX: bgScale, y: bgScale))
        bg = bg.transformed(by: CGAffineTransform(translationX: (render.width - sw * bgScale) / 2,
                                                  y: (render.height - sh * bgScale) / 2))
        bg = bg.clampedToExtent()
            .applyingFilter("CIGaussianBlur", parameters: [kCIInputRadiusKey: 32])
            .cropped(to: rect)

        // Foreground: fit to the width, centered vertically, sharp.
        let fgScale = render.width / sw
        var fg = img.transformed(by: CGAffineTransform(scaleX: fgScale, y: fgScale))
        fg = fg.transformed(by: CGAffineTransform(translationX: 0,
                                                  y: (render.height - sh * fgScale) / 2))
        return fg.composited(over: bg).cropped(to: rect)
    }

    // MARK: - Captions (one per clip, centered lower-third)

    private static func addCaptions(to videoComp: AVMutableVideoComposition,
                                    placed: [(range: CMTimeRange, text: String)],
                                    renderSize: CGSize, total: CMTime) {
        let parent = CALayer()
        parent.frame = CGRect(origin: .zero, size: renderSize)
        let videoLayer = CALayer()
        videoLayer.frame = parent.frame
        parent.addSublayer(videoLayer)

        let margin = renderSize.width * 0.06
        for (range, text) in placed where !text.trimmingCharacters(in: .whitespaces).isEmpty {
            let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
            let fontSize = renderSize.width * 0.052
            let label = CATextLayer()
            label.string = styled(text, fontSize: fontSize)
            label.isWrapped = true
            label.alignmentMode = .center
            label.contentsScale = UIScreen.main.scale
            let height = renderSize.height * 0.30
            label.frame = CGRect(x: margin, y: renderSize.height * 0.12,
                                 width: renderSize.width - margin * 2, height: height)
            label.shadowColor = UIColor.black.cgColor
            label.shadowOpacity = 0.9
            label.shadowRadius = 6
            label.shadowOffset = .zero

            // Show only during this clip's slice of the merged timeline.
            let begin = range.start.seconds
            let dur = range.duration.seconds
            label.opacity = 0
            let show = CAKeyframeAnimation(keyPath: "opacity")
            show.values = [0, 1, 1, 0]
            show.keyTimes = [0, 0.02, 0.98, 1]
            show.beginTime = begin == 0 ? AVCoreAnimationBeginTimeAtZero : begin
            show.duration = dur
            show.isRemovedOnCompletion = false
            show.fillMode = .forwards
            label.add(show, forKey: "vis")
            parent.addSublayer(label)
        }

        videoComp.animationTool = AVVideoCompositionCoreAnimationTool(
            postProcessingAsVideoLayer: videoLayer, in: parent)
    }

    private static func styled(_ text: String, fontSize: CGFloat) -> NSAttributedString {
        let style = NSMutableParagraphStyle()
        style.alignment = .center
        style.lineBreakMode = .byWordWrapping
        return NSAttributedString(string: text, attributes: [
            .font: UIFont.systemFont(ofSize: fontSize, weight: .heavy),
            .foregroundColor: UIColor.white,
            .paragraphStyle: style,
        ])
    }

    // MARK: - Export

    private static func export(_ composition: AVMutableComposition,
                               videoComp: AVMutableVideoComposition,
                               name: String,
                               progress: ((Double, String?) -> Void)?) async throws -> URL {
        guard let session = AVAssetExportSession(asset: composition,
                                                 presetName: AVAssetExportPreset1920x1080) else {
            throw LocalVideoError.exportFailed("no export session")
        }
        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(name)-\(UUID().uuidString).mp4")
        session.outputURL = out
        session.outputFileType = .mp4
        session.videoComposition = videoComp
        session.shouldOptimizeForNetworkUse = true

        await session.export()
        switch session.status {
        case .completed:
            progress?(1, "Done")
            return out
        case .failed, .cancelled:
            throw LocalVideoError.exportFailed(session.error?.localizedDescription ?? "unknown")
        default:
            throw LocalVideoError.exportFailed("status \(session.status.rawValue)")
        }
    }
}
#endif
