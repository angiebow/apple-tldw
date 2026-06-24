//
//  SummarizationService.swift
//  tldw — Overall Summary (BART)
//
//  Thin HTTP client for the local FastAPI BART sidecar.
//

import Foundation

enum SummarizationError: LocalizedError {
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

struct SummarizationService {
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

    /// Send text to BART and return the summary. When `reference` is non-nil,
    /// the response also carries ROUGE / BERTScore / METEOR `metrics`.
    func summarize(
        _ text: String,
        maxLength: Int = 130,
        minLength: Int = 30,
        reference: String? = nil
    ) async throws -> SummarizeResponse {
        var request = URLRequest(url: baseURL.appendingPathComponent("summarize"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 300  // first call downloads ~1.6GB of weights
        request.httpBody = try JSONEncoder().encode(
            SummarizeRequest(text: text, maxLength: maxLength, minLength: minLength, reference: reference)
        )

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw SummarizationError.transport(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw SummarizationError.transport("no HTTP response")
        }
        guard http.statusCode == 200 else {
            throw SummarizationError.badStatus(http.statusCode, serverDetail(data))
        }

        do {
            return try JSONDecoder().decode(SummarizeResponse.self, from: data)
        } catch {
            throw SummarizationError.transport("could not decode response: \(error.localizedDescription)")
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
