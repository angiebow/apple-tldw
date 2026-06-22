//
//  SegmentationService.swift
//  tldw — Phase 1: Topic Segmentation
//
//  Thin HTTP client for the local FastAPI BERTopic sidecar.
//

import Foundation

enum SegmentationError: LocalizedError {
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

struct SegmentationService {
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

    /// Send the transcript to BERTopic and return contiguous topic segments.
    func segment(_ utterances: [Utterance], minTopicSize: Int = 2) async throws -> SegmentResponse {
        var request = URLRequest(url: baseURL.appendingPathComponent("segment"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 180  // first call downloads the embedding model
        request.httpBody = try JSONEncoder().encode(
            SegmentRequest(utterances: utterances, minTopicSize: minTopicSize)
        )

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw SegmentationError.transport(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw SegmentationError.transport("no HTTP response")
        }
        guard http.statusCode == 200 else {
            throw SegmentationError.badStatus(http.statusCode, serverDetail(data))
        }

        do {
            return try JSONDecoder().decode(SegmentResponse.self, from: data)
        } catch {
            throw SegmentationError.transport("could not decode response: \(error.localizedDescription)")
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
