//
//  ContentView.swift
//  tldw — Transcript Content Highlighter PoC
//
//  Two states share one window:
//   • input    — paste a transcript and generate "Shorts"
//   • results  — "Here are your Shorts": a card grid of the top-ranked lines
//

import SwiftUI
import AppKit

struct ContentView: View {
    @State private var model = HighlightViewModel()
    @State private var category: Category = .viral
    @State private var showSearch = false
    @State private var searchText = ""
    @AppStorage("isDarkMode") private var isDarkMode = false

    enum Category: String, CaseIterable {
        case viral = "Most viral"
        case relevant = "Most relevant"
    }

    var body: some View {
        ZStack {
            Color(nsColor: .windowBackgroundColor).ignoresSafeArea()
            Group {
                if model.isLoading {
                    loadingView
                } else if model.hasResults {
                    resultsView
                } else {
                    inputView
                }
            }
        }
        .frame(minWidth: 920, minHeight: 620)
        .preferredColorScheme(isDarkMode ? .dark : .light)
        .task { await model.checkHealth() }
    }

    /// Top-right light/dark toggle. Shows the icon of the mode you'll switch to.
    private var themeToggle: some View {
        Button { withAnimation { isDarkMode.toggle() } } label: {
            Image(systemName: isDarkMode ? "sun.max.fill" : "moon.fill")
                .font(.title3)
        }
        .buttonStyle(.borderless)
        .help(isDarkMode ? "Switch to light mode" : "Switch to dark mode")
    }

    // MARK: - Shared bits

    private var serverPill: some View {
        HStack(spacing: 6) {
            Circle().fill(serverColor).frame(width: 8, height: 8)
            Text(serverLabel).font(.caption).foregroundStyle(.secondary)
            Button { Task { await model.checkHealth() } } label: {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(.borderless)
            .help("Re-check backend at 127.0.0.1:8000")
        }
        .padding(.horizontal, 10).padding(.vertical, 5)
        .background(.quaternary, in: Capsule())
    }

    private var serverColor: Color {
        switch model.serverReachable {
        case .some(true): return .green
        case .some(false): return .red
        case .none: return .yellow
        }
    }

    private var serverLabel: String {
        switch model.serverReachable {
        case .some(true): return "backend up"
        case .some(false): return "backend down"
        case .none: return "checking…"
        }
    }

    // MARK: - Loading

    private var loadingView: some View {
        VStack(spacing: 14) {
            ProgressView()
            Text("Generating your Shorts…")
                .font(.title3.weight(.medium))
            Text(model.statusMessage)
                .font(.callout).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 420)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Input state

    private var inputView: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top) {
                titleBlock(emoji: "🩳", title: "Generate Shorts",
                           subtitle: "Paste a transcript, then find the most relevant and viral-worthy lines.")
                Spacer()
                HStack(spacing: 10) {
                    serverPill
                    themeToggle
                }
            }

            TextEditor(text: $model.transcriptText)
                .font(.callout)
                .padding(12)
                .scrollContentBackground(.hidden)
                .background(Color(nsColor: .controlBackgroundColor),
                            in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(.quaternary))
                .frame(maxHeight: .infinity)

            HStack {
                Text("\(model.transcriptText.count) characters")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button { model.loadSample() } label: {
                    Label("Sample", systemImage: "doc.text")
                }
                Button { Task { await model.findHighlights() } } label: {
                    Label("Generate Shorts", systemImage: "sparkles")
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.transcriptText.trimmingCharacters(in: .whitespacesAndNewlines).count < 40)
            }
        }
        .padding(28)
    }

    // MARK: - Results state ("Here are your Shorts")

    private var shownLines: [LineScore] {
        let base = category == .viral ? model.viralLines : model.relevantLines
        guard !searchText.isEmpty else { return base }
        return base.filter { $0.text.localizedCaseInsensitiveContains(searchText) }
    }

    private var resultsView: some View {
        VStack(alignment: .leading, spacing: 0) {
            resultsHeader
            ScrollView {
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 300), spacing: 18)],
                    spacing: 18
                ) {
                    ForEach(Array(shownLines.enumerated()), id: \.element.id) { idx, line in
                        ShortCard(rank: idx + 1, line: line, category: category)
                    }
                }
                .padding(28)
            }
        }
    }

    private var resultsHeader: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                titleBlock(emoji: "🩳", title: "Here are your Shorts",
                           subtitle: "We have generated the following Shorts for this video, check them out")
                Spacer()
                HStack(spacing: 10) {
                    serverPill
                    Button { withAnimation { showSearch.toggle() } } label: {
                        Image(systemName: "magnifyingglass")
                            .font(.title3)
                    }
                    .buttonStyle(.borderless)
                    .help("Search lines")
                    themeToggle
                }
            }

            if !model.summary.isEmpty {
                Text("Summary: \(model.summary)")
                    .font(.callout).foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            HStack(spacing: 12) {
                Picker("", selection: $category) {
                    ForEach(Category.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .fixedSize()

                if showSearch {
                    TextField("Search lines…", text: $searchText)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 260)
                        .transition(.opacity)
                }

                Spacer()

                Button { model.loadSample() } label: {
                    Label("New transcript", systemImage: "arrow.uturn.backward")
                }
                .buttonStyle(.borderless)
            }
        }
        .padding(.horizontal, 28)
        .padding(.top, 24)
        .padding(.bottom, 8)
    }

    // MARK: - Title block

    private func titleBlock(emoji: String, title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 12) {
                Text(emoji).font(.system(size: 34))
                Text(title).font(.system(size: 34, weight: .bold))
            }
            Text(subtitle)
                .font(.title3)
                .foregroundStyle(.secondary)
        }
    }
}

// MARK: - Short card

private struct ShortCard: View {
    let rank: Int
    let line: LineScore
    let category: ContentView.Category

    /// Estimated spoken duration of the line at ~150 words/minute (2.5 w/s).
    /// Real durations come from the audio/timestamp step (out of scope here).
    private var estSeconds: Int {
        let words = line.text.split(whereSeparator: { $0 == " " || $0 == "\n" }).count
        return max(1, Int((Double(words) / 2.5).rounded()))
    }

    private var primaryScore: Double {
        category == .viral ? line.viralScore : line.relevance
    }

    private var label: String {
        let tag = category == .viral ? "VIRAL" : "RELEVANT"
        return String(format: "#%d · %@ %.2f", rank, tag, primaryScore)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 8) {
                Text(label)
                    .font(.caption.weight(.semibold))
                    .tracking(0.8)
                    .foregroundStyle(.secondary)
                if line.viralLabel {
                    Image(systemName: "checkmark.seal.fill")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                        .help("Detector: viral-worthy")
                }
            }

            Text("“\(line.text)”")
                .font(.system(size: 20, weight: .bold))
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)

            Spacer(minLength: 0)

            HStack {
                Label("\(estSeconds) seconds long", systemImage: "clock")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    let pb = NSPasteboard.general
                    pb.clearContents()
                    pb.setString(line.text, forType: .string)
                } label: {
                    Image(systemName: "arrow.right")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 30, height: 30)
                        .background(Circle().fill(Color.blue))
                }
                .buttonStyle(.plain)
                .help("Copy line text")
            }
        }
        .padding(22)
        .frame(maxWidth: .infinity, minHeight: 210, alignment: .topLeading)
        .background(Color(nsColor: .controlBackgroundColor),
                    in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(.quaternary, lineWidth: 1))
        .shadow(color: .black.opacity(0.06), radius: 8, y: 2)
    }
}

#Preview {
    ContentView()
}
