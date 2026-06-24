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
    @State private var showTranscript = false
    @State private var showSummary = false
    @State private var selectedOrder: [Int] = []   // line ids, in the order picked
    @State private var detailLine: LineScore?
    @State private var showEditor = false
    @AppStorage("isDarkMode") private var isDarkMode = false

    /// All ranked lines keyed by their global line index (both lists share the
    /// same `lines` array, so ids are consistent across categories).
    private var linesByID: [Int: LineScore] {
        var map: [Int: LineScore] = [:]
        for l in model.relevantLines + model.viralLines { map[l.id] = l }
        return map
    }

    /// The selected lines as ordered clips for the editor.
    private var selectedClips: [LineScore] {
        selectedOrder.compactMap { linesByID[$0] }
    }

    private func toggleSelection(_ id: Int, _ on: Bool) {
        if on {
            if !selectedOrder.contains(id) { selectedOrder.append(id) }
        } else {
            selectedOrder.removeAll { $0 == id }
            if selectedOrder.isEmpty { showEditor = false }
        }
    }

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
                } else if showEditor && !selectedClips.isEmpty {
                    EditorView(
                        clips: selectedClips,
                        onBack: { showEditor = false },
                        onRemove: { id in toggleSelection(id, false) },
                        onMove: { from, to in selectedOrder.move(fromOffsets: IndexSet(integer: from), toOffset: to) }
                    )
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
                        ShortCard(rank: idx + 1, line: line, category: category,
                                  isSelected: Binding(
                                    get: { selectedOrder.contains(line.id) },
                                    set: { toggleSelection(line.id, $0) }
                                  ),
                                  onOpen: { detailLine = line })
                    }
                }
                .padding(28)
            }
        }
        .sheet(item: $detailLine) { line in
            ShortDetailView(line: line, category: category)
        }
    }

    private var resultsHeader: some View {
        VStack(alignment: .leading, spacing: 14) {
            resultsTopBar
            disclosure(label: "Original transcript", systemImage: "text.alignleft",
                       isOpen: $showTranscript, text: model.transcriptText)
            if !model.summary.isEmpty {
                disclosure(label: "Summary", systemImage: "text.append",
                           isOpen: $showSummary, text: model.summary)
            }
            resultsControls
        }
        .padding(.horizontal, 28)
        .padding(.top, 24)
        .padding(.bottom, 8)
    }

    private var resultsTopBar: some View {
        HStack(alignment: .top) {
            titleBlock(emoji: "🩳", title: "Here are your Shorts",
                       subtitle: "We have generated the following Shorts for this video, check them out")
            Spacer()
            HStack(spacing: 10) {
                serverPill
                Button { withAnimation { showSearch.toggle() } } label: {
                    Image(systemName: "magnifyingglass").font(.title3)
                }
                .buttonStyle(.borderless)
                .help("Search lines")
                themeToggle
            }
        }
    }

    private func disclosure(label: String, systemImage: String,
                            isOpen: Binding<Bool>, text: String) -> some View {
        DisclosureGroup(isExpanded: isOpen) {
            ScrollView {
                Text(text)
                    .font(.callout)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
            }
            .frame(maxHeight: 200)
            .background(Color(nsColor: .controlBackgroundColor),
                        in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(.quaternary))
            .padding(.top, 6)
        } label: {
            Label(label, systemImage: systemImage).font(.callout.weight(.medium))
        }
    }

    private var resultsControls: some View {
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

            if !selectedOrder.isEmpty {
                Button { showEditor = true } label: {
                    Label("Edit \(selectedOrder.count) selected", systemImage: "slider.horizontal.below.rectangle")
                }
                .buttonStyle(.borderedProminent)
            }

            Button { model.loadSample() } label: {
                Label("New transcript", systemImage: "arrow.uturn.backward")
            }
            .buttonStyle(.borderless)
        }
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

/// Estimated spoken duration of a line at ~150 words/minute (2.5 w/s).
/// Real durations come from the audio/timestamp step (out of scope here).
private func estimatedSeconds(_ text: String) -> Int {
    let words = text.split(whereSeparator: { $0 == " " || $0 == "\n" }).count
    return max(1, Int((Double(words) / 2.5).rounded()))
}

private struct ShortCard: View {
    let rank: Int
    let line: LineScore
    let category: ContentView.Category
    @Binding var isSelected: Bool
    let onOpen: () -> Void

    private var estSeconds: Int { estimatedSeconds(line.text) }

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
                    isSelected.toggle()
                } label: {
                    Image(systemName: isSelected ? "checkmark.square.fill" : "square")
                        .font(.system(size: 24))
                        .foregroundStyle(isSelected ? Color.blue : Color.secondary)
                }
                .buttonStyle(.plain)
                .help(isSelected ? "Selected" : "Select this Short")
            }
        }
        .padding(22)
        .frame(maxWidth: .infinity, minHeight: 210, alignment: .topLeading)
        .background(Color(nsColor: .controlBackgroundColor),
                    in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(.quaternary, lineWidth: 1))
        .shadow(color: .black.opacity(0.06), radius: 8, y: 2)
        .contentShape(RoundedRectangle(cornerRadius: 14))
        .onTapGesture { onOpen() }
        .help("Open full line")
    }
}

// MARK: - Short detail (full line + video preview placeholder)

private struct ShortDetailView: View {
    let line: LineScore
    let category: ContentView.Category
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            // Video clip preview — placeholder until the audio/cut step exists.
            ZStack {
                RoundedRectangle(cornerRadius: 12)
                    .fill(Color(nsColor: .controlBackgroundColor))
                RoundedRectangle(cornerRadius: 12)
                    .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
                    .foregroundStyle(.quaternary)
                VStack(spacing: 8) {
                    Image(systemName: "play.rectangle.fill")
                        .font(.system(size: 46))
                        .foregroundStyle(.secondary)
                    Text("Video clip preview")
                        .font(.headline).foregroundStyle(.secondary)
                    Text("Generated once the audio extraction & cutting step is wired up")
                        .font(.caption).foregroundStyle(.tertiary)
                        .multilineTextAlignment(.center)
                }
                .padding()
            }
            .frame(height: 220)

            // Full line text.
            ScrollView {
                Text("“\(line.text)”")
                    .font(.title3.weight(.semibold))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 180)

            // Metadata.
            HStack(spacing: 10) {
                Label("\(estimatedSeconds(line.text)) seconds long", systemImage: "clock")
                    .foregroundStyle(.secondary)
                if line.viralLabel {
                    Label("viral-worthy", systemImage: "checkmark.seal.fill")
                        .foregroundStyle(.orange)
                }
                Spacer()
                scorePill("relevance", line.relevance)
                scorePill("viral", line.viralScore)
            }
            .font(.callout)

            Spacer(minLength: 0)

            HStack {
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(width: 560, height: 560)
    }

    private func scorePill(_ label: String, _ value: Double) -> some View {
        HStack(spacing: 4) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(String(format: "%.2f", value)).font(.caption.monospacedDigit())
        }
        .padding(.horizontal, 8).padding(.vertical, 3)
        .background(Color.secondary.opacity(0.15), in: Capsule())
    }
}

// MARK: - Editor page

private struct EditorView: View {
    let clips: [LineScore]                 // selected lines, in arranged order
    let onBack: () -> Void
    let onRemove: (Int) -> Void            // by line id
    let onMove: (Int, Int) -> Void         // (fromOffset, toOffset)

    @State private var previewID: Int?

    private var currentClip: LineScore? {
        clips.first { $0.id == previewID } ?? clips.first
    }

    private var totalSeconds: Int {
        clips.reduce(0) { $0 + estimatedSeconds($1.text) }
    }

    var body: some View {
        VStack(spacing: 0) {
            topBar
            Divider()
            previewArea
                .padding(24)
            Spacer(minLength: 0)
            Divider()
            strip
        }
    }

    private var topBar: some View {
        HStack {
            Button { onBack() } label: { Label("Back", systemImage: "chevron.left") }
                .buttonStyle(.borderless)
            Spacer()
            VStack(spacing: 1) {
                Text("Editor").font(.headline)
                Text("\(clips.count) clip\(clips.count == 1 ? "" : "s") · total ≈ \(totalSeconds)s")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button { } label: { Label("Share", systemImage: "square.and.arrow.up") }
                .disabled(true)
                .help("Sharing page — coming next")
        }
        .padding()
    }

    private var previewArea: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 14)
                .fill(Color(nsColor: .controlBackgroundColor))
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
                .foregroundStyle(.quaternary)
            VStack(spacing: 14) {
                Image(systemName: "play.rectangle.fill")
                    .font(.system(size: 50)).foregroundStyle(.secondary)
                Text("Edit result preview")
                    .font(.headline).foregroundStyle(.secondary)
                if let c = currentClip {
                    Text("“\(c.text)”")
                        .font(.title3.weight(.semibold))
                        .multilineTextAlignment(.center)
                        .textSelection(.enabled)
                        .frame(maxWidth: 620)
                    Text("clip ≈ \(estimatedSeconds(c.text)) seconds")
                        .font(.caption).foregroundStyle(.tertiary)
                }
                Text("Rendered video preview arrives with the audio/cutting step")
                    .font(.caption2).foregroundStyle(.tertiary)
            }
            .padding(32)
        }
        .frame(maxWidth: .infinity, minHeight: 320)
    }

    private var strip: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Arrange your Shorts")
                .font(.callout.weight(.medium))
                .padding(.horizontal, 24).padding(.top, 10)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 12) {
                    ForEach(Array(clips.enumerated()), id: \.element.id) { i, clip in
                        ClipThumb(
                            order: i + 1,
                            clip: clip,
                            isCurrent: clip.id == (currentClip?.id ?? -1),
                            canMoveLeft: i > 0,
                            canMoveRight: i < clips.count - 1,
                            onSelect: { previewID = clip.id },
                            onRemove: { onRemove(clip.id) },
                            onLeft: { onMove(i, i - 1) },
                            onRight: { onMove(i, i + 2) }
                        )
                    }
                }
                .padding(.horizontal, 24).padding(.bottom, 16)
            }
        }
        .background(.bar)
    }
}

private struct ClipThumb: View {
    let order: Int
    let clip: LineScore
    let isCurrent: Bool
    let canMoveLeft: Bool
    let canMoveRight: Bool
    let onSelect: () -> Void
    let onRemove: () -> Void
    let onLeft: () -> Void
    let onRight: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("#\(order)")
                    .font(.caption.monospacedDigit().weight(.bold))
                    .foregroundStyle(.secondary)
                Spacer()
                Button { onRemove() } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.plain).foregroundStyle(.secondary)
                    .help("Remove from editor")
            }

            // Thumbnail placeholder.
            ZStack {
                RoundedRectangle(cornerRadius: 8).fill(.quaternary)
                Image(systemName: "play.fill").foregroundStyle(.secondary)
            }
            .frame(height: 64)

            Text(clip.text)
                .font(.caption)
                .lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)

            HStack {
                Button { onLeft() } label: { Image(systemName: "arrow.left") }
                    .buttonStyle(.plain).disabled(!canMoveLeft)
                Label("\(estimatedSeconds(clip.text))s", systemImage: "clock")
                    .font(.caption2).foregroundStyle(.secondary)
                Spacer()
                Button { onRight() } label: { Image(systemName: "arrow.right") }
                    .buttonStyle(.plain).disabled(!canMoveRight)
            }
        }
        .padding(12)
        .frame(width: 200)
        .background(Color(nsColor: .controlBackgroundColor),
                    in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10)
            .stroke(isCurrent ? Color.accentColor : Color.clear, lineWidth: 2))
        .contentShape(RoundedRectangle(cornerRadius: 10))
        .onTapGesture { onSelect() }
    }
}

#Preview {
    ContentView()
}
