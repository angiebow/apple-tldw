//
//  HFTranscriber.swift
//  tldw (iOS)
//
//  Transcription via the free Hugging Face Inference API (Whisper). The video's
//  audio is extracted on-device to a small m4a, sent to the hosted Whisper model,
//  and the returned phrase chunks (with [start,end] timestamps) become the
//  TranscriptSegments the ranker + editor use. Whisper is "one model, one
//  input→output", so it fits the free Inference tier — unlike the video pipeline.
//

#if os(iOS)
import AVFoundation
import CoreMedia
import Foundation
import tldwKit

struct HFTranscriber {
    // whisper-large-v3 on the default hf-inference provider.
    private static let endpoint = URL(string:
        "https://router.huggingface.co/hf-inference/models/openai/whisper-large-v3")!

    struct Result { let text: String; let segments: [TranscriptSegment] }

    static func transcribe(videoURL: URL,
                           progress: ((Double, String?) -> Void)? = nil) async throws -> Result {
        guard let token = BackendConfig.hfToken else {
            throw HighlightError.transport("No Hugging Face token set (HFToken in BackendConfig.plist).")
        }
        progress?(0.1, "Extracting audio…")
        let audio = try await extractWAV(from: videoURL)
        defer { try? FileManager.default.removeItem(at: audio) }

        progress?(0.3, "Transcribing on Hugging Face…")
        let data = try Data(contentsOf: audio)
        let body: [String: Any] = [
            "inputs": data.base64EncodedString(),
            "parameters": ["return_timestamps": true],
        ]
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (respData, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw HighlightError.transport("no response")
        }
        guard http.statusCode == 200 else {
            let detail = String(data: respData, encoding: .utf8) ?? ""
            if http.statusCode == 503 {
                throw HighlightError.badStatus(503, "Whisper is warming up on Hugging Face — try again in a moment.")
            }
            throw HighlightError.badStatus(http.statusCode, detail)
        }

        progress?(0.9, "Reading transcript…")
        return try parse(respData)
    }

    // MARK: audio extraction (→ 16 kHz mono 16-bit WAV)
    //
    // HF's Whisper decodes with libsndfile, which does NOT support m4a/AAC — so we
    // read the video's audio track as PCM (resampled to 16 kHz mono, Whisper's
    // native rate — also keeps the upload small) and wrap it in a WAV container.

    private static let sampleRate = 16_000

    private static func extractWAV(from videoURL: URL) async throws -> URL {
        let asset = AVURLAsset(url: videoURL)
        guard let track = try await asset.loadTracks(withMediaType: .audio).first else {
            throw HighlightError.transport("that recording has no audio track")
        }
        let reader = try AVAssetReader(asset: asset)
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: sampleRate,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
            AVLinearPCMIsNonInterleaved: false,
        ]
        let output = AVAssetReaderTrackOutput(track: track, outputSettings: settings)
        reader.add(output)
        guard reader.startReading() else {
            throw HighlightError.transport(reader.error?.localizedDescription ?? "couldn't read audio")
        }

        var pcm = Data()
        while reader.status == .reading, let sample = output.copyNextSampleBuffer() {
            if let block = CMSampleBufferGetDataBuffer(sample) {
                var length = 0
                var ptr: UnsafeMutablePointer<Int8>?
                CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil,
                                            totalLengthOut: &length, dataPointerOut: &ptr)
                if let ptr { pcm.append(UnsafeBufferPointer(start: UnsafePointer(ptr), count: length)) }
            }
            CMSampleBufferInvalidate(sample)
        }
        guard reader.status == .completed else {
            throw HighlightError.transport(reader.error?.localizedDescription ?? "audio read failed")
        }

        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent("vireel-audio-\(UUID().uuidString).wav")
        try wavData(pcm: pcm).write(to: out)
        return out
    }

    /// Wrap little-endian 16-bit mono PCM in a minimal 44-byte WAV header.
    private static func wavData(pcm: Data) -> Data {
        let channels: UInt16 = 1, bits: UInt16 = 16
        let rate = UInt32(sampleRate)
        let byteRate = rate * UInt32(channels) * UInt32(bits / 8)
        let blockAlign = channels * (bits / 8)
        var d = Data()
        func str(_ s: String) { d.append(s.data(using: .ascii)!) }
        func u32(_ v: UInt32) { var x = v.littleEndian; withUnsafeBytes(of: &x) { d.append(contentsOf: $0) } }
        func u16(_ v: UInt16) { var x = v.littleEndian; withUnsafeBytes(of: &x) { d.append(contentsOf: $0) } }
        str("RIFF"); u32(UInt32(36 + pcm.count)); str("WAVE")
        str("fmt "); u32(16); u16(1); u16(channels); u32(rate); u32(byteRate); u16(blockAlign); u16(bits)
        str("data"); u32(UInt32(pcm.count))
        d.append(pcm)
        return d
    }

    // MARK: response parsing

    private struct ASRResponse: Decodable {
        struct Chunk: Decodable { let timestamp: [Double?]; let text: String }
        let text: String
        let chunks: [Chunk]?
    }

    private static func parse(_ data: Data) throws -> Result {
        let decoded = try JSONDecoder().decode(ASRResponse.self, from: data)
        let full = decoded.text.trimmingCharacters(in: .whitespacesAndNewlines)

        var segments: [TranscriptSegment] = []
        if let chunks = decoded.chunks {
            var lastEnd = 0.0
            for (i, c) in chunks.enumerated() {
                let start = c.timestamp.first.flatMap { $0 } ?? lastEnd
                // Whisper can emit a null end on the final chunk — carry the start.
                let end = (c.timestamp.count > 1 ? c.timestamp[1] : nil) ?? (start + 3)
                lastEnd = end
                let t = c.text.trimmingCharacters(in: .whitespaces)
                guard !t.isEmpty, end > start else { continue }
                segments.append(TranscriptSegment(id: i, start: start, end: end, text: t, words: nil))
            }
        }
        // No chunks (shouldn't happen with return_timestamps) → one whole segment.
        if segments.isEmpty, !full.isEmpty {
            segments = [TranscriptSegment(id: 0, start: 0, end: 0, text: full, words: nil)]
        }
        return Result(text: full, segments: segments)
    }
}
#endif
