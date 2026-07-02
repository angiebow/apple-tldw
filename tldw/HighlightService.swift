//
//  HighlightService.swift
//  tldw — Transcript Content Highlighter
//
//  Thin HTTP client for the local FastAPI highlighter sidecar.
//

import Foundation

enum HighlightError: LocalizedError {
    case badStatus(Int, String)
    case transport(String)

    var errorDescription: String? {
        switch self {
        case .badStatus(let code, let detail):
            return "Server returned \(code): \(detail)"
        case .transport(let message):
            return "Cannot reach the backend (\(message)). Is ./run.sh running on port 8000?"
        }
    }
}

struct HighlightService {
    var baseURL = URL(string: "http://127.0.0.1:8000")!

    /// Quick liveness probe for the status pill.
    func health() async -> Bool {
        var request = URLRequest(url: baseURL.appendingPathComponent("health"))
        request.timeoutInterval = 3
        guard let (_, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse else {
            return false
        }
        return http.statusCode == 200
    }

    /// Run the highlighter pipeline and return the two ranked lists. Pass the
    /// transcribe `segments` to get timestamped results (spans the editor can cut).
    func highlight(_ text: String,
                   title: String? = nil,
                   segments: [TranscriptSegment]? = nil,
                   topK: Int = 10) async throws -> HighlightResponse {
        var request = URLRequest(url: baseURL.appendingPathComponent("highlight"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 600  // first call downloads Pegasus + mpnet weights

        request.httpBody = try JSONEncoder().encode(
            HighlightRequest(text: text, topK: topK, title: title, segments: segments))
 
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw HighlightError.transport(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw HighlightError.transport("no HTTP response")
        }
        guard http.statusCode == 200 else {
            throw HighlightError.badStatus(http.statusCode, serverDetail(data))
        }

        do {
            return try JSONDecoder().decode(HighlightResponse.self, from: data)
        } catch {
            throw HighlightError.transport("could not decode response: \(error.localizedDescription)")
        }
    }

    /// Generate an emotion-matched background music bed for a clip (editor page).
    func generateBacksound(for line: LineScore) async throws -> BacksoundResponse {
        var request = URLRequest(url: baseURL.appendingPathComponent("backsound"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 600  // first call downloads MusicGen weights

        request.httpBody = try JSONEncoder().encode(
            BacksoundRequest(text: line.text, durationS: nil))

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw HighlightError.transport(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw HighlightError.transport("no HTTP response")
        }
        guard http.statusCode == 200 else {
            throw HighlightError.badStatus(http.statusCode, serverDetail(data))
        }

        do {
            return try JSONDecoder().decode(BacksoundResponse.self, from: data)
        } catch {
            throw HighlightError.transport("could not decode backsound response: \(error.localizedDescription)")
        }
    }

    /// Detect non-speech "blooper" spans in a local video file.
    func detectBloopers(videoPath: String,
                        useLipCheck: Bool = true) async throws -> BlooperResponse {
        var request = URLRequest(url: baseURL.appendingPathComponent("bloopers"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 600  // first call downloads Silero VAD + decodes the whole video

        request.httpBody = try JSONEncoder().encode(
            BlooperRequest(videoPath: videoPath, useLipCheck: useLipCheck))

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw HighlightError.transport(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw HighlightError.transport("no HTTP response")
        }
        guard http.statusCode == 200 else {
            throw HighlightError.badStatus(http.statusCode, serverDetail(data))
        }

        do {
            return try JSONDecoder().decode(BlooperResponse.self, from: data)
        } catch {
            throw HighlightError.transport("could not decode bloopers response: \(error.localizedDescription)")
        }
    }

    /// Transcribe a local video/audio file into text (preprocess + VAD-guided Whisper).
    func transcribe(videoPath: String,
                    model: String? = nil) async throws -> TranscribeResponse {
        var request = URLRequest(url: baseURL.appendingPathComponent("transcribe"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 1800  // first call downloads Whisper + decodes/transcribes the whole file

        request.httpBody = try JSONEncoder().encode(
            TranscribeRequest(videoPath: videoPath, model: model))

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw HighlightError.transport(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw HighlightError.transport("no HTTP response")
        }
        guard http.statusCode == 200 else {
            throw HighlightError.badStatus(http.statusCode, serverDetail(data))
        }

        do {
            return try JSONDecoder().decode(TranscribeResponse.self, from: data)
        } catch {
            throw HighlightError.transport("could not decode transcribe response: \(error.localizedDescription)")
        }
    }

    /// Cut the given spans out of the source video into .mp4 files on disk.
    /// Defaults to portrait Shorts with karaoke captions; pass `segments` (with
    /// word times) so each clip can be captioned.
    func exportClips(videoPath: String,
                     clips: [ClipSpan],
                     segments: [TranscriptSegment]? = nil,
                     vertical: Bool = true,
                     subtitles: Bool = true,
                     name: String? = nil) async throws -> ClipResponse {
        var request = URLRequest(url: baseURL.appendingPathComponent("clip"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 900  // re-encoding several spans can take a while

        request.httpBody = try JSONEncoder().encode(
            ClipRequest(videoPath: videoPath, clips: clips, name: name,
                        vertical: vertical, subtitles: subtitles, segments: segments))

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw HighlightError.transport(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw HighlightError.transport("no HTTP response")
        }
        guard http.statusCode == 200 else {
            throw HighlightError.badStatus(http.statusCode, serverDetail(data))
        }

        do {
            return try JSONDecoder().decode(ClipResponse.self, from: data)
        } catch {
            throw HighlightError.transport("could not decode clip response: \(error.localizedDescription)")
        }
    }

    /// Concatenate the given spans into one portrait+captioned Short on disk.
    /// Pass `segments` (with word times) so the merged Short can be captioned.
    func mergeClips(videoPath: String,
                    clips: [ClipSpan],
                    segments: [TranscriptSegment]? = nil,
                    vertical: Bool = true,
                    subtitles: Bool = true,
                    name: String? = nil) async throws -> MergeResponse {
        var request = URLRequest(url: baseURL.appendingPathComponent("merge"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 900  // re-encoding + concatenating several spans can take a while

        request.httpBody = try JSONEncoder().encode(
            MergeRequest(videoPath: videoPath, clips: clips, name: name,
                         vertical: vertical, subtitles: subtitles, segments: segments))

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw HighlightError.transport(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw HighlightError.transport("no HTTP response")
        }
        guard http.statusCode == 200 else {
            throw HighlightError.badStatus(http.statusCode, serverDetail(data))
        }

        do {
            return try JSONDecoder().decode(MergeResponse.self, from: data)
        } catch {
            throw HighlightError.transport("could not decode merge response: \(error.localizedDescription)")
        }
    }

    /// Pull the human-readable `detail` field out of a FastAPI error body.
    private func serverDetail(_ data: Data) -> String {
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let detail = obj["detail"] as? String {
            return detail
        }
        return String(data: data, encoding: .utf8) ?? "unknown error"
    }
}
