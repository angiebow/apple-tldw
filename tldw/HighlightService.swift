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

    /// Run the highlighter pipeline and return the two ranked lists.
    func highlight(_ text: String, topK: Int = 10) async throws -> HighlightResponse {
        var request = URLRequest(url: baseURL.appendingPathComponent("highlight"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 600  // first call downloads Pegasus + mpnet weights

        request.httpBody = try JSONEncoder().encode(HighlightRequest(text: text, topK: topK))

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

    /// Pull the human-readable `detail` field out of a FastAPI error body.
    private func serverDetail(_ data: Data) -> String {
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let detail = obj["detail"] as? String {
            return detail
        }
        return String(data: data, encoding: .utf8) ?? "unknown error"
    }
}
