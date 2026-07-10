//
//  HighlightService.swift
//  tldw — Transcript Content Highlighter
//
//  Client for the ViReel cloud backend. Text-only calls (/highlight, /backsound)
//  are synchronous; media calls (transcribe, bloopers, clip, merge) upload the
//  file, enqueue a job, poll it, and download any results — so the same client
//  works for both the macOS and iOS apps against a hosted backend.
//

import Foundation

public enum HighlightError: LocalizedError {
    case badStatus(Int, String)
    case transport(String)

    public var errorDescription: String? {
        switch self {
        case .badStatus(let code, let detail):
            return "Server returned \(code): \(detail)"
        case .transport(let message):
            return "Cannot reach the backend (\(message))."
        }
    }
}

public struct HighlightService {
    public var baseURL: URL
    public var apiToken: String?

    public init(baseURL: URL = BackendConfig.baseURL,
                apiToken: String? = BackendConfig.apiToken) {
        self.baseURL = baseURL
        self.apiToken = apiToken
    }

    private var client: CloudClient { CloudClient(baseURL: baseURL, token: apiToken) }

    /// Quick liveness probe for the status pill.
    public func health() async -> Bool {
        var request = URLRequest(url: baseURL.appendingPathComponent("health"))
        request.timeoutInterval = 3
        guard let (_, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse else {
            return false
        }
        return http.statusCode == 200
    }

    // MARK: - Synchronous, text-only

    /// Run the highlighter pipeline and return the two ranked lists. Pass the
    /// transcribe `segments` to get timestamped results (spans the editor can cut).
    public func highlight(_ text: String,
                          title: String? = nil,
                          segments: [TranscriptSegment]? = nil,
                          topK: Int = 10) async throws -> HighlightResponse {
        try await client.postJSON(
            "highlight",
            HighlightRequest(text: text, topK: topK, title: title, segments: segments),
            timeout: 600)  // first call downloads the summarizer + embedder weights
    }

    /// Generate an emotion-matched background music bed for a clip (editor page).
    public func generateBacksound(for line: LineScore) async throws -> BacksoundResponse {
        try await client.postJSON(
            "backsound",
            BacksoundRequest(text: line.text, durationS: nil),
            timeout: 600)  // first call downloads MusicGen weights
    }

    // MARK: - Media jobs (upload → job → poll → download)

    /// Detect non-speech "blooper" spans in a video file.
    public func detectBloopers(videoPath: String,
                               useLipCheck: Bool = true,
                               progress: ((Double, String?) -> Void)? = nil) async throws -> BlooperResponse {
        let key = try await client.uploadFile(URL(fileURLWithPath: videoPath))
        let jobId = try await client.createJob(
            type: "bloopers", mediaKey: key, params: BloopersParams(useLipCheck: useLipCheck))
        let result: JobResult<BlooperResponse> =
            try await client.pollResult(jobId: jobId, as: BlooperResponse.self, progress: progress)
        guard let data = result.data else {
            throw HighlightError.transport("bloopers job returned no data")
        }
        return data
    }

    /// Transcribe a video/audio file into text (Whisper).
    public func transcribe(videoPath: String,
                           model: String? = nil,
                           progress: ((Double, String?) -> Void)? = nil) async throws -> TranscribeResponse {
        let key = try await client.uploadFile(URL(fileURLWithPath: videoPath))
        let jobId = try await client.createJob(
            type: "transcribe", mediaKey: key, params: TranscribeParams(model: model))
        let result: JobResult<TranscribeResponse> =
            try await client.pollResult(jobId: jobId, as: TranscribeResponse.self, progress: progress)
        guard let data = result.data else {
            throw HighlightError.transport("transcribe job returned no data")
        }
        return data
    }

    /// Cut the given spans into standalone .mp4 files. The finished clips are
    /// downloaded to a temp folder; `ClipResponse.clips[].path` points at them.
    public func exportClips(videoPath: String,
                            clips: [ClipSpan],
                            segments: [TranscriptSegment]? = nil,
                            vertical: Bool = true,
                            subtitles: Bool = true,
                            name: String? = nil,
                            outputDir: String? = nil,
                            progress: ((Double, String?) -> Void)? = nil) async throws -> ClipResponse {
        let key = try await client.uploadFile(URL(fileURLWithPath: videoPath))
        let jobId = try await client.createJob(
            type: "clip", mediaKey: key,
            params: ClipParams(clips: clips, name: name, vertical: vertical,
                               subtitles: subtitles, srt: true, segments: segments))
        let result: JobResult<ClipJobData> =
            try await client.pollResult(jobId: jobId, as: ClipJobData.self, progress: progress)
        guard let data = result.data else {
            throw HighlightError.transport("clip job returned no data")
        }

        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("vireel-clips/\(result.id)")
        var infos: [ClipInfo] = []
        for item in data.clips {
            guard let out = result.outputs.first(where: { $0.key == item.key }) else { continue }
            let ext = (item.key as NSString).pathExtension.isEmpty ? "mp4" : (item.key as NSString).pathExtension
            let local = try await client.download(out.downloadURL, to: dir,
                                                  filename: "clip_\(item.index).\(ext)")
            if let srtKey = item.srtKey,
               let srtOut = result.outputs.first(where: { $0.key == srtKey }) {
                _ = try? await client.download(srtOut.downloadURL, to: dir,
                                               filename: "clip_\(item.index).srt")
            }
            infos.append(ClipInfo(index: item.index, path: local.path,
                                  start: item.start, end: item.end, text: item.text))
        }
        return ClipResponse(source: data.source, outputDir: dir.path, count: infos.count,
                            vertical: data.vertical, subtitlesApplied: data.subtitlesApplied,
                            subtitlesRequested: data.subtitlesRequested, clips: infos)
    }

    /// Concatenate the given spans into one Short. The merged file is downloaded
    /// to a temp folder; `MergeResponse.path` points at it.
    public func mergeClips(videoPath: String,
                           clips: [ClipSpan],
                           segments: [TranscriptSegment]? = nil,
                           vertical: Bool = true,
                           subtitles: Bool = true,
                           name: String? = nil,
                           outputDir: String? = nil,
                           music: [MusicPlacement]? = nil,
                           progress: ((Double, String?) -> Void)? = nil) async throws -> MergeResponse {
        let key = try await client.uploadFile(URL(fileURLWithPath: videoPath))
        let jobId = try await client.createJob(
            type: "merge", mediaKey: key,
            params: MergeParams(clips: clips, name: name, vertical: vertical,
                                subtitles: subtitles, music: music, segments: segments))
        let result: JobResult<MergeJobData> =
            try await client.pollResult(jobId: jobId, as: MergeJobData.self, progress: progress)
        guard let data = result.data, let out = result.outputs.first else {
            throw HighlightError.transport("merge job returned no output")
        }

        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("vireel-merge/\(result.id)")
        let stem = name ?? "\(data.source)_merged"
        let local = try await client.download(out.downloadURL, to: dir, filename: "\(stem).mp4")
        return MergeResponse(source: data.source, outputDir: dir.path, path: local.path,
                             clipCount: data.clipCount, vertical: data.vertical,
                             subtitlesApplied: data.subtitlesApplied,
                             subtitlesRequested: data.subtitlesRequested)
    }
}

// MARK: - Job param bodies (snake_case keys the backend reads)

private struct TranscribeParams: Encodable {
    let model: String?
}

private struct BloopersParams: Encodable {
    let useLipCheck: Bool
    enum CodingKeys: String, CodingKey { case useLipCheck = "use_lip_check" }
}

private struct ClipParams: Encodable {
    let clips: [ClipSpan]
    let name: String?
    let vertical: Bool
    let subtitles: Bool
    let srt: Bool
    let segments: [TranscriptSegment]?
}

private struct MergeParams: Encodable {
    let clips: [ClipSpan]
    let name: String?
    let vertical: Bool
    let subtitles: Bool
    let music: [MusicPlacement]?
    let segments: [TranscriptSegment]?
}

// MARK: - Cloud job result payloads (map onto the public response types)

private struct ClipJobData: Codable {
    let source: String
    let count: Int
    let vertical: Bool
    let subtitlesApplied: Bool
    let subtitlesRequested: Bool
    let srtCount: Int
    let clips: [ClipJobItem]

    enum CodingKeys: String, CodingKey {
        case source, count, vertical, clips
        case subtitlesApplied = "subtitles_applied"
        case subtitlesRequested = "subtitles_requested"
        case srtCount = "srt_count"
    }
}

private struct ClipJobItem: Codable {
    let index: Int
    let key: String
    let start: Double
    let end: Double
    let text: String
    let srtKey: String?

    enum CodingKeys: String, CodingKey {
        case index, key, start, end, text
        case srtKey = "srt_key"
    }
}

private struct MergeJobData: Codable {
    let source: String
    let clipCount: Int
    let vertical: Bool
    let subtitlesApplied: Bool
    let subtitlesRequested: Bool

    enum CodingKeys: String, CodingKey {
        case source, vertical
        case clipCount = "clip_count"
        case subtitlesApplied = "subtitles_applied"
        case subtitlesRequested = "subtitles_requested"
    }
}
