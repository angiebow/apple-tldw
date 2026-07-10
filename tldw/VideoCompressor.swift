//
//  VideoCompressor.swift
//  tldw (iOS)
//
//  Transcodes a picked video to a smaller 720p H.264 MP4 before upload. Phone
//  videos (4K/HEVC) are hundreds of MB, which blows past tunnel/proxy upload
//  limits and is needlessly heavy for Shorts. 720p keeps uploads small + fast
//  and doesn't hurt the transcript or the vertical clip output.
//

#if os(iOS)
import AVFoundation
import Foundation
import tldwKit

enum VideoCompressor {
    /// Returns a compressed copy, or the original URL if no export session is
    /// available for the asset.
    static func compress(_ input: URL) async throws -> URL {
        let asset = AVURLAsset(url: input)
        let preset = AVAssetExportPreset1280x720
        guard AVAssetExportSession.exportPresets(compatibleWith: asset).contains(preset),
              let export = AVAssetExportSession(asset: asset, presetName: preset) else {
            return input
        }
        let output = FileManager.default.temporaryDirectory
            .appendingPathComponent("vireel-compressed-\(UUID().uuidString).mp4")
        try? FileManager.default.removeItem(at: output)
        export.outputURL = output
        export.outputFileType = .mp4
        export.shouldOptimizeForNetworkUse = true

        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            export.exportAsynchronously { continuation.resume() }
        }

        switch export.status {
        case .completed:
            return output
        case .cancelled:
            throw HighlightError.transport("Video preparation was cancelled.")
        default:
            throw export.error ?? HighlightError.transport("Couldn't prepare the video.")
        }
    }
}
#endif
