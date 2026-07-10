//
//  CloudClient.swift
//  tldwKit
//
//  Low-level client for the async cloud API: mint upload → PUT media → create
//  job → poll status → fetch result / download outputs. See
//  backend/cloud/openapi.yaml. HighlightService builds its public API on top.
//

import Foundation

// MARK: - Wire types

struct UploadResponse: Codable {
    let mediaKey: String
    let uploadURL: String
    let expiresIn: Int

    enum CodingKeys: String, CodingKey {
        case mediaKey = "media_key"
        case uploadURL = "upload_url"
        case expiresIn = "expires_in"
    }
}

struct JobCreateBody<P: Encodable>: Encodable {
    let type: String
    let mediaKey: String
    let params: P

    enum CodingKeys: String, CodingKey {
        case type, params
        case mediaKey = "media_key"
    }
}

struct JobView: Codable {
    let id: String
    let type: String
    let status: String
    let progress: Double
    let stage: String?
    let error: String?
}

struct JobOutput: Codable {
    let key: String
    let kind: String
    let bytes: Int?
    let downloadURL: String

    enum CodingKeys: String, CodingKey {
        case key, kind, bytes
        case downloadURL = "download_url"
    }
}

struct JobResult<T: Codable>: Codable {
    let id: String
    let type: String
    let status: String
    let data: T?
    let outputs: [JobOutput]
}

// MARK: - Client

struct CloudClient {
    let baseURL: URL
    let token: String?
    let session: URLSession

    init(baseURL: URL, token: String?, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.token = token
        self.session = session
    }

    private func authorize(_ request: inout URLRequest) {
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
    }

    // MARK: generic JSON send

    func postJSON<Body: Encodable, R: Decodable>(_ path: String, _ body: Body,
                                                 timeout: TimeInterval = 600) async throws -> R {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = timeout
        authorize(&request)
        request.httpBody = try JSONEncoder().encode(body)
        return try await send(request)
    }

    func getJSON<R: Decodable>(_ path: String, timeout: TimeInterval = 30) async throws -> R {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.timeoutInterval = timeout
        authorize(&request)
        return try await send(request)
    }

    private func send<R: Decodable>(_ request: URLRequest) async throws -> R {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw HighlightError.transport(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw HighlightError.transport("no HTTP response")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw HighlightError.badStatus(http.statusCode, serverDetail(data))
        }
        do {
            return try JSONDecoder().decode(R.self, from: data)
        } catch {
            throw HighlightError.transport("could not decode response: \(error.localizedDescription)")
        }
    }

    // MARK: upload → job → poll → download

    /// Mint an upload target and PUT the file's bytes (streamed from disk).
    func uploadFile(_ fileURL: URL) async throws -> String {
        struct Mint: Encodable { let filename: String; let content_type: String }
        let minted: UploadResponse = try await postJSON(
            "uploads", Mint(filename: fileURL.lastPathComponent, content_type: mimeType(for: fileURL)),
            timeout: 30)

        guard let putURL = URL(string: minted.uploadURL) else {
            throw HighlightError.transport("backend returned an invalid upload URL")
        }
        var request = URLRequest(url: putURL)
        request.httpMethod = "PUT"
        request.timeoutInterval = 3600
        authorize(&request)  // ignored by presigned S3; used by the dev /storage route

        let response: URLResponse
        do {
            (_, response) = try await session.upload(for: request, fromFile: fileURL)
        } catch {
            throw HighlightError.transport(error.localizedDescription)
        }
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw HighlightError.badStatus(http.statusCode, "upload failed")
        }
        return minted.mediaKey
    }

    func createJob<P: Encodable>(type: String, mediaKey: String, params: P) async throws -> String {
        let job: JobView = try await postJSON(
            "jobs", JobCreateBody(type: type, mediaKey: mediaKey, params: params), timeout: 30)
        return job.id
    }

    /// Poll until the job finishes; return its typed result envelope. `progress`
    /// is called on each poll with (fraction 0–1, stage) for a live UI.
    func pollResult<T: Codable>(jobId: String, as _: T.Type,
                                pollInterval: TimeInterval = 1.5,
                                progress: ((Double, String?) -> Void)? = nil) async throws -> JobResult<T> {
        while true {
            let view: JobView = try await getJSON("jobs/\(jobId)")
            progress?(view.progress, view.stage)
            switch view.status {
            case "done":
                return try await getJSON("jobs/\(jobId)/result")
            case "error":
                throw HighlightError.badStatus(500, view.error ?? "job failed")
            default:
                try await Task.sleep(nanoseconds: UInt64(pollInterval * 1_000_000_000))
            }
        }
    }

    /// Download an output URL to `dir/filename`, returning the local file URL.
    func download(_ urlString: String, to dir: URL, filename: String) async throws -> URL {
        guard let url = URL(string: urlString) else {
            throw HighlightError.transport("backend returned an invalid download URL")
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 3600
        authorize(&request)

        let tempURL: URL
        let response: URLResponse
        do {
            (tempURL, response) = try await session.download(for: request)
        } catch {
            throw HighlightError.transport(error.localizedDescription)
        }
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw HighlightError.badStatus(http.statusCode, "download failed")
        }
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let dest = dir.appendingPathComponent(filename)
        try? FileManager.default.removeItem(at: dest)
        try FileManager.default.moveItem(at: tempURL, to: dest)
        return dest
    }
}

// MARK: - Shared helpers

/// Pull the human-readable `detail` field out of a FastAPI error body.
func serverDetail(_ data: Data) -> String {
    if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
       let detail = obj["detail"] as? String {
        return detail
    }
    return String(data: data, encoding: .utf8) ?? "unknown error"
}

func mimeType(for url: URL) -> String {
    switch url.pathExtension.lowercased() {
    case "mp4", "m4v": return "video/mp4"
    case "mov":        return "video/quicktime"
    case "webm":       return "video/webm"
    case "mkv":        return "video/x-matroska"
    case "avi":        return "video/x-msvideo"
    case "m4a":        return "audio/mp4"
    case "mp3":        return "audio/mpeg"
    case "wav":        return "audio/wav"
    default:           return "application/octet-stream"
    }
}
