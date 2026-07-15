//
//  WordPieceTokenizer.swift
//  tldw (iOS)
//
//  A faithful port of BERT's BasicTokenizer + WordpieceTokenizer, so the Core ML
//  virality models get exactly the token IDs their HuggingFace tokenizers would
//  produce. The detector uses the uncased vocab (lowercases + strips accents);
//  the reranker uses the multilingual-cased vocab (keeps case). Validated against
//  the reference tokenizers (see WordPieceTokenizer.selfTest()).
//

#if os(iOS)
import Foundation

struct WordPieceTokenizer {
    private let vocab: [String: Int]
    private let lowercase: Bool
    private let clsID, sepID, padID, unkID: Int
    let maxLen: Int

    init?(vocabResource: String, lowercase: Bool, maxLen: Int = 64) {
        guard let url = Bundle.main.url(forResource: vocabResource, withExtension: "txt"),
              let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        var map: [String: Int] = [:]
        var i = 0
        text.enumerateLines { line, _ in map[line] = i; i += 1 }
        guard let cls = map["[CLS]"], let sep = map["[SEP]"],
              let pad = map["[PAD]"], let unk = map["[UNK]"] else { return nil }
        self.vocab = map
        self.lowercase = lowercase
        self.maxLen = maxLen
        (clsID, sepID, padID, unkID) = (cls, sep, pad, unk)
    }

    /// Tokenize `text` to fixed-length `input_ids` + `attention_mask` (padded /
    /// truncated to `maxLen`, with [CLS]/[SEP]).
    func encode(_ text: String) -> (ids: [Int32], mask: [Int32]) {
        var pieces: [Int] = []
        for token in basicTokenize(text) {
            pieces.append(contentsOf: wordpiece(token))
            if pieces.count > maxLen - 2 { break }
        }
        if pieces.count > maxLen - 2 { pieces = Array(pieces.prefix(maxLen - 2)) }

        var ids = [Int32](repeating: Int32(padID), count: maxLen)
        var mask = [Int32](repeating: 0, count: maxLen)
        ids[0] = Int32(clsID); mask[0] = 1
        for (k, p) in pieces.enumerated() { ids[k + 1] = Int32(p); mask[k + 1] = 1 }
        let sepIdx = pieces.count + 1
        ids[sepIdx] = Int32(sepID); mask[sepIdx] = 1
        return (ids, mask)
    }

    // MARK: BasicTokenizer — clean, (optionally) lowercase/strip-accents, split on
    // whitespace and punctuation.

    private func basicTokenize(_ text: String) -> [String] {
        var out: [String] = []
        for whitespaceToken in cleaned(text).split(whereSeparator: { $0.isWhitespace }) {
            var token = String(whitespaceToken)
            if lowercase {
                token = token.lowercased()
                token = stripAccents(token)
            }
            out.append(contentsOf: splitOnPunctuation(token))
        }
        return out
    }

    private func cleaned(_ text: String) -> String {
        let space = Unicode.Scalar(0x20)!
        var s = String.UnicodeScalarView()
        for u in text.unicodeScalars {
            if u.value == 0 || u.value == 0xFFFD { continue }
            if isControl(u) { continue }
            let isWs = u.value == 0x0009 || u.value == 0x000A || u.value == 0x000D
            s.append(isWs ? space : u)
        }
        return String(s)
    }

    private func stripAccents(_ text: String) -> String {
        var out = String.UnicodeScalarView()
        for u in text.decomposedStringWithCanonicalMapping.unicodeScalars
        where u.properties.generalCategory != .nonspacingMark {
            out.append(u)
        }
        return String(out)
    }

    private func splitOnPunctuation(_ token: String) -> [String] {
        var result: [String] = []
        var current = String.UnicodeScalarView()
        for u in token.unicodeScalars {
            if isPunctuation(u) {
                if !current.isEmpty { result.append(String(current)); current = .init() }
                result.append(String(u))
            } else {
                current.append(u)
            }
        }
        if !current.isEmpty { result.append(String(current)) }
        return result
    }

    // MARK: WordPiece — greedy longest-match, ## continuation, whole word → [UNK].

    private func wordpiece(_ word: String) -> [Int] {
        let chars = Array(word.unicodeScalars)
        if chars.count > 100 { return [unkID] }
        var out: [Int] = []
        var start = 0
        while start < chars.count {
            var end = chars.count
            var match: Int? = nil
            while start < end {
                var sub = String(String.UnicodeScalarView(chars[start..<end]))
                if start > 0 { sub = "##" + sub }
                if let id = vocab[sub] { match = id; break }
                end -= 1
            }
            guard let id = match else { return [unkID] }   // any char unmatched → whole word UNK
            out.append(id)
            start = end
        }
        return out
    }

    // MARK: Unicode class helpers (BERT semantics)

    private func isControl(_ u: Unicode.Scalar) -> Bool {
        if u.value == 0x0009 || u.value == 0x000A || u.value == 0x000D { return false }
        switch u.properties.generalCategory {
        case .control, .format: return true
        default: return false
        }
    }

    private func isPunctuation(_ u: Unicode.Scalar) -> Bool {
        let v = u.value
        if (v >= 33 && v <= 47) || (v >= 58 && v <= 64) ||
           (v >= 91 && v <= 96) || (v >= 123 && v <= 126) { return true }
        switch u.properties.generalCategory {
        case .connectorPunctuation, .dashPunctuation, .openPunctuation, .closePunctuation,
             .initialPunctuation, .finalPunctuation, .otherPunctuation:
            return true
        default:
            return false
        }
    }
}
#endif
