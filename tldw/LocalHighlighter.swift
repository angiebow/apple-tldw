//
//  LocalHighlighter.swift
//  tldw (iOS)
//
//  On-device replacement for the backend /highlight call. Groups the transcript
//  segments into scenes, scores each for virality with the two trained Core ML
//  models (detector + reranker — your models, converted + quantized), and scores
//  relevance with Apple's built-in NLEmbedding (no bundled model, no LLM summary).
//  Produces the same two ranked LineScore lists the app already renders.
//

#if os(iOS)
import CoreML
import Foundation
import NaturalLanguage
import tldwKit

struct HighlightResult {
    var viral: [LineScore]
    var relevant: [LineScore]
    var lineCount: Int
}

final class LocalHighlighter {
    static let shared = LocalHighlighter()

    // Scene grouping — mirrors the backend's 10–60s target.
    private let minScene = 8.0
    private let maxScene = 60.0

    private var detector: MLModel?
    private var reranker: MLModel?
    private let detectorTok = WordPieceTokenizer(vocabResource: "vocab-detector", lowercase: true)
    private let rerankerTok = WordPieceTokenizer(vocabResource: "vocab-reranker", lowercase: false)

    /// Load the Core ML models (compiled .mlmodelc in the app bundle). Lazy so the
    /// cost is only paid on first ranking.
    private func loadModels() throws {
        if detector == nil {
            detector = try model(named: "distilbert-detector")
        }
        if reranker == nil {
            reranker = try model(named: "bert-ranker")
        }
    }

    private func model(named name: String) throws -> MLModel {
        guard let url = Bundle.main.url(forResource: name, withExtension: "mlmodelc") else {
            throw HighlightError.transport("bundled model \(name) missing")
        }
        let cfg = MLModelConfiguration()
        // CPU only: the multilingual reranker's large int8 embedding table makes
        // the Neural Engine/GPU compiler OOM at first predict. CPU is more than
        // fast enough for a handful of short scene texts.
        cfg.computeUnits = .cpuOnly
        return try MLModel(contentsOf: url, configuration: cfg)
    }

    func rank(segments: [TranscriptSegment], topK: Int = 10) throws -> HighlightResult {
        try loadModels()
        let scenes = buildScenes(segments)
        guard !scenes.isEmpty else { return HighlightResult(viral: [], relevant: [], lineCount: 0) }

        let texts = scenes.map(\.text)
        let relevance = relevanceScores(texts)

        var rows: [LineScore] = []
        for (i, scene) in scenes.enumerated() {
            let (prob, label) = try detect(scene.text)
            let score = try rerank(scene.text)
            rows.append(LineScore(index: i, text: scene.text, relevance: relevance[i],
                                  viralScore: score, viralProb: prob, viralLabel: label,
                                  start: scene.start, end: scene.end))
        }

        let k = max(1, min(topK, rows.count))
        // Viral: detector gates, reranker orders the survivors (backend cascade).
        let viral = rows.filter { $0.viralLabel }
            .sorted { $0.viralScore > $1.viralScore }
            .prefix(k)
        // If the detector gated everything out, fall back to top reranker scores so
        // the user still gets suggestions.
        let viralList = viral.isEmpty
            ? Array(rows.sorted { $0.viralScore > $1.viralScore }.prefix(k))
            : Array(viral)
        let relevant = rows.sorted { $0.relevance > $1.relevance }.prefix(k)
        return HighlightResult(viral: viralList, relevant: Array(relevant), lineCount: rows.count)
    }

    // MARK: Scene grouping (duration + sentence boundaries)

    private struct Scene { var text: String; var start: Double; var end: Double }

    private func buildScenes(_ segments: [TranscriptSegment]) -> [Scene] {
        var scenes: [Scene] = []
        var cur: Scene?
        for seg in segments {
            let t = seg.text.trimmingCharacters(in: .whitespaces)
            guard !t.isEmpty else { continue }
            if cur == nil { cur = Scene(text: t, start: seg.start, end: seg.end); continue }
            cur!.text += " " + t
            cur!.end = seg.end
            let dur = cur!.end - cur!.start
            let endsSentence = t.hasSuffix(".") || t.hasSuffix("!") || t.hasSuffix("?")
            if (dur >= minScene && endsSentence) || dur >= maxScene {
                scenes.append(cur!); cur = nil
            }
        }
        if let last = cur { // keep a trailing partial scene if it's long enough
            if last.end - last.start >= minScene || scenes.isEmpty { scenes.append(last) }
            else if !scenes.isEmpty {
                scenes[scenes.count - 1].text += " " + last.text
                scenes[scenes.count - 1].end = last.end
            }
        }
        return scenes
    }

    // MARK: Relevance via NLEmbedding (cosine to the transcript centroid)

    private func relevanceScores(_ texts: [String]) -> [Double] {
        guard let embedder = NLEmbedding.sentenceEmbedding(for: .english) else {
            return Array(repeating: 0.5, count: texts.count)
        }
        let vectors = texts.map { embedder.vector(for: $0) ?? [] }
        let dim = vectors.first(where: { !$0.isEmpty })?.count ?? 0
        guard dim > 0 else { return Array(repeating: 0.5, count: texts.count) }

        var centroid = [Double](repeating: 0, count: dim)
        var n = 0
        for v in vectors where v.count == dim { for j in 0..<dim { centroid[j] += v[j] }; n += 1 }
        if n > 0 { for j in 0..<dim { centroid[j] /= Double(n) } }

        let raw = vectors.map { v -> Double in
            v.count == dim ? cosine(v, centroid) : 0
        }
        // Normalize to 0–1 so the score bar reads sensibly.
        let lo = raw.min() ?? 0, hi = raw.max() ?? 1
        let span = max(1e-6, hi - lo)
        return raw.map { ($0 - lo) / span }
    }

    private func cosine(_ a: [Double], _ b: [Double]) -> Double {
        var dot = 0.0, na = 0.0, nb = 0.0
        for i in 0..<a.count { dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i] }
        return dot / (sqrt(na) * sqrt(nb) + 1e-9)
    }

    // MARK: Core ML inference

    private func detect(_ text: String) throws -> (prob: Double, label: Bool) {
        guard let tok = detectorTok, let model = detector else {
            throw HighlightError.transport("detector unavailable")
        }
        let arr = try predict(model, tok.encode(text), maxLen: tok.maxLen)
        let p0 = arr[0].doubleValue, p1 = arr[1].doubleValue
        return (p1, p1 > p0)
    }

    private func rerank(_ text: String) throws -> Double {
        guard let tok = rerankerTok, let model = reranker else {
            throw HighlightError.transport("reranker unavailable")
        }
        let arr = try predict(model, tok.encode(text), maxLen: tok.maxLen)
        return arr[0].doubleValue
    }

    private func predict(_ model: MLModel, _ enc: (ids: [Int32], mask: [Int32]),
                         maxLen: Int) throws -> MLMultiArray {
        let ids = try multiArray(enc.ids, len: maxLen)
        let mask = try multiArray(enc.mask, len: maxLen)
        let input = try MLDictionaryFeatureProvider(dictionary: [
            "input_ids": ids, "attention_mask": mask,
        ])
        let out = try model.prediction(from: input)
        guard let name = out.featureNames.first,
              let arr = out.featureValue(for: name)?.multiArrayValue else {
            throw HighlightError.transport("model produced no output")
        }
        return arr
    }

    private func multiArray(_ values: [Int32], len: Int) throws -> MLMultiArray {
        let arr = try MLMultiArray(shape: [1, NSNumber(value: len)], dataType: .int32)
        for i in 0..<len { arr[i] = NSNumber(value: values[i]) }
        return arr
    }
}
#endif
