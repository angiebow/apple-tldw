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
import AVFoundation
import AVKit

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

    /// What the big preview is showing — a sentence clip (text) or a blooper (video).
    private enum Selection: Hashable {
        case sentence(Int)   // LineScore id
        case blooper(Int)    // BlooperSpan id
    }
    @State private var selection: Selection?
    @State private var marked: Set<Selection> = []   // clips ticked for merging
    @State private var showMergePreview = false

    // Detected blooper (dead-air) spans, surfaced into the same timeline sequence.
    @State private var bloopers: [BlooperSpan] = []
    @State private var blooperVideoURL: URL?
    @State private var bigPlayer = AVPlayer()       // drives the top preview for blooper spans
    @State private var bigEndObserver: Any?

    /// The marked clips in timeline order (sentences first, then bloopers).
    private var mergeItems: [MergeClip] {
        clips.filter { marked.contains(.sentence($0.id)) }.map { MergeClip.sentence($0) }
        + bloopers.filter { marked.contains(.blooper($0.id)) }.map { MergeClip.blooper($0) }
    }

    // Background music beds are generated per clip, on demand, for the selected clip.
    @State private var bedByClip: [Int: BacksoundResponse] = [:]
    @State private var bedGeneratingID: Int?
    @State private var bedError: String?
    @State private var player: AVAudioPlayer?
    private let service = HighlightService()

    /// The sentence clip the preview/controls act on (nil while a blooper is selected).
    private var currentClip: LineScore? {
        switch selection {
        case .sentence(let id): return clips.first { $0.id == id }
        case .blooper:          return nil
        case nil:               return clips.first
        }
    }

    /// The blooper span currently shown in the preview, if any.
    private var currentBlooper: BlooperSpan? {
        if case let .blooper(id) = selection { return bloopers.first { $0.id == id } }
        return nil
    }

    private func generateBacksound(for clip: LineScore) {
        bedGeneratingID = clip.id
        bedError = nil
        Task {
            defer { bedGeneratingID = nil }
            do {
                bedByClip[clip.id] = try await service.generateBacksound(for: clip)
            } catch {
                bedError = error.localizedDescription
            }
        }
    }

    private func playAudio(b64: String, onError: (String) -> Void) {
        guard let data = Data(base64Encoded: b64) else {
            onError("Could not decode audio.")
            return
        }
        do {
            player = try AVAudioPlayer(data: data)
            player?.play()
        } catch {
            onError("Could not play audio: \(error.localizedDescription)")
        }
    }

    private func selectSentence(_ clip: LineScore) {
        selection = .sentence(clip.id)
        stopSpan()
    }

    /// Select a blooper clip and play its span in the big preview.
    private func selectBlooper(_ span: BlooperSpan) {
        selection = .blooper(span.id)
        guard let item = bigPlayer.currentItem else { return }
        stopSpan()
        let start = CMTime(seconds: span.start, preferredTimescale: 600)
        let end = CMTime(seconds: span.end, preferredTimescale: 600)
        bigPlayer.pause()
        item.seek(to: start, toleranceBefore: .zero, toleranceAfter: .zero) { _ in
            bigPlayer.play()
        }
        bigEndObserver = bigPlayer.addBoundaryTimeObserver(
            forTimes: [NSValue(time: end)], queue: .main
        ) { [weak bigPlayer] in bigPlayer?.pause() }
    }

    private func stopSpan() {
        bigPlayer.pause()
        if let bigEndObserver { bigPlayer.removeTimeObserver(bigEndObserver); self.bigEndObserver = nil }
    }

    /// Receive detected spans (and their source video) from the panel.
    private func receiveBloopers(_ spans: [BlooperSpan], _ url: URL?) {
        bloopers = spans
        blooperVideoURL = url
        if let url { bigPlayer.replaceCurrentItem(with: AVPlayerItem(url: url)) }
        // If the selected blooper vanished after a re-scan, fall back to a sentence.
        if case let .blooper(id) = selection, !spans.contains(where: { $0.id == id }) {
            selection = clips.first.map { .sentence($0.id) }
            stopSpan()
        }
    }

    /// Spoken length of the sentence clips (shown in the header).
    private var totalSeconds: Int {
        clips.reduce(0) { $0 + estimatedSeconds($1.text) }
    }

    /// Full timeline length including the appended blooper spans (drives the ruler).
    private var timelineSeconds: Int {
        totalSeconds + Int(bloopers.reduce(0.0) { $0 + $1.duration }.rounded())
    }

    var body: some View {
        if showMergePreview {
            MergePreviewView(items: mergeItems, videoURL: blooperVideoURL,
                             onBack: { showMergePreview = false })
        } else {
            editorBody
        }
    }

    private var editorBody: some View {
        VStack(spacing: 0) {
            topBar
            Divider()
            previewArea
                .padding(24)
            backsoundBar
            Spacer(minLength: 0)
            Divider()
            strip
            Divider()
            // Detected spans flow up into the timeline above (same sequence).
            BlooperPanel(onBloopers: receiveBloopers)
        }
        .onAppear { if selection == nil, let f = clips.first { selection = .sentence(f.id) } }
        .onDisappear { stopSpan() }
    }

    private func toggleMark(_ key: Selection) {
        if marked.contains(key) { marked.remove(key) } else { marked.insert(key) }
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

    @ViewBuilder
    private var previewArea: some View {
        Group {
            if let span = currentBlooper, blooperVideoURL != nil {
                blooperPreview(span)
            } else {
                sentencePreview
            }
        }
        .frame(maxWidth: .infinity, minHeight: 320)
    }

    /// The blooper span playing in place from its source video.
    private func blooperPreview(_ span: BlooperSpan) -> some View {
        ZStack(alignment: .bottom) {
            VideoPlayer(player: bigPlayer)
                .background(Color.black)

            HStack(spacing: 8) {
                Image(systemName: "waveform.badge.exclamationmark")
                Text("Blooper · \(span.label.replacingOccurrences(of: "_", with: " ")) · \(String(format: "%.2fs", span.duration))")
                Spacer()
                Button { selectBlooper(span) } label: { Label("Replay", systemImage: "arrow.clockwise") }
                    .buttonStyle(.borderless)
            }
            .font(.caption.weight(.medium))
            .foregroundStyle(.white)
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background(.black.opacity(0.55))
        }
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    /// The text placeholder for a selected sentence clip (no real footage yet).
    private var sentencePreview: some View {
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
    }

    /// Background-music controls for the selected clip — emotion-matched bed.
    @ViewBuilder
    private var backsoundBar: some View {
        if let clip = currentClip {
            let bed = bedByClip[clip.id]
            HStack(spacing: 12) {
                Image(systemName: "music.note")
                    .font(.title3)
                    .foregroundStyle(bed == nil ? .secondary : Color.purple)

                VStack(alignment: .leading, spacing: 2) {
                    Text("Background music").font(.callout.weight(.semibold))
                    if let err = bedError {
                        Text(err).font(.caption).foregroundStyle(.red).lineLimit(1)
                    } else if let bed {
                        Text("\(bed.emotion) · V \(bed.valence, specifier: "%.2f") · A \(bed.arousal, specifier: "%.2f")")
                            .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    } else {
                        Text("Generate an emotion-matched music bed")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }

                Spacer()

                if bedGeneratingID == clip.id {
                    ProgressView().controlSize(.small)
                } else {
                    Button { generateBacksound(for: clip) } label: {
                        Label(bed == nil ? "Generate music" : "Regenerate", systemImage: "wand.and.stars")
                    }
                    .buttonStyle(.bordered)
                }

                if let bed {
                    Button { playAudio(b64: bed.audioB64) { bedError = $0 } } label: {
                        Label("Play", systemImage: "play.fill")
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            .background(Color(nsColor: .controlBackgroundColor),
                        in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(.quaternary))
            .padding(.horizontal, 24)
        }
    }

    private let pxPerSec: CGFloat = 26
    private func clipWidth(_ c: LineScore) -> CGFloat {
        max(90, CGFloat(estimatedSeconds(c.text)) * pxPerSec)
    }
    private func bloopWidth(_ b: BlooperSpan) -> CGFloat {
        max(54, CGFloat(b.duration) * pxPerSec)
    }
    private var contentWidth: CGFloat {
        let sentences = clips.reduce(0) { $0 + clipWidth($1) }
        let dead = bloopers.reduce(0) { $0 + bloopWidth($1) }
        let count = clips.count + bloopers.count
        return sentences + dead + CGFloat(max(0, count - 1)) * 2
    }

    private var strip: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Text("Timeline").font(.callout.weight(.semibold)).foregroundStyle(.white)
                Spacer()
                if !marked.isEmpty {
                    Button { showMergePreview = true } label: {
                        Label("Merge \(marked.count) clip\(marked.count == 1 ? "" : "s")",
                              systemImage: "rectangle.stack.badge.play")
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                }
                selectedControls
            }
            .padding(.horizontal, 20).padding(.top, 12)

            ScrollView(.horizontal, showsIndicators: true) {
                VStack(alignment: .leading, spacing: 6) {
                    TimelineRuler(width: contentWidth, totalSeconds: timelineSeconds, pxPerSec: pxPerSec)
                    HStack(spacing: 2) {
                        ForEach(clips) { clip in
                            TimelineClip(clip: clip,
                                         width: clipWidth(clip),
                                         isCurrent: selection == .sentence(clip.id),
                                         isMarked: marked.contains(.sentence(clip.id)),
                                         onSelect: { selectSentence(clip) },
                                         onMark: { toggleMark(.sentence(clip.id)) },
                                         onRemove: { onRemove(clip.id) })
                        }
                        ForEach(bloopers) { span in
                            BlooperTimelineClip(span: span,
                                                width: bloopWidth(span),
                                                isCurrent: selection == .blooper(span.id),
                                                isMarked: marked.contains(.blooper(span.id)),
                                                onSelect: { selectBlooper(span) },
                                                onMark: { toggleMark(.blooper(span.id)) },
                                                onRemove: {
                                                    bloopers.removeAll { $0.id == span.id }
                                                    marked.remove(.blooper(span.id))
                                                    if selection == .blooper(span.id) {
                                                        selection = clips.first.map { .sentence($0.id) }
                                                        stopSpan()
                                                    }
                                                })
                        }
                    }
                    WaveformTrack(width: contentWidth)
                }
                .padding(.horizontal, 20).padding(.bottom, 16)
            }
        }
        .background(Color(white: 0.12))
    }

    @ViewBuilder
    private var selectedControls: some View {
        if let c = currentClip, let i = clips.firstIndex(where: { $0.id == c.id }) {
            HStack(spacing: 8) {
                Button { onMove(i, i - 1) } label: { Image(systemName: "arrow.left") }
                    .disabled(i == 0)
                Button { onRemove(c.id) } label: { Image(systemName: "trash") }
                Button { onMove(i, i + 2) } label: { Image(systemName: "arrow.right") }
                    .disabled(i == clips.count - 1)
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
    }
}

/// A single clip block on the timeline — width scales with its duration, with a
/// filmstrip-style thumbnail band over a colored title bar.
private struct TimelineClip: View {
    let clip: LineScore
    let width: CGFloat
    let isCurrent: Bool
    let isMarked: Bool
    let onSelect: () -> Void
    let onMark: () -> Void
    let onRemove: () -> Void

    private var thumbCount: Int { max(1, Int(width / 26)) }

    var body: some View {
        VStack(spacing: 0) {
            // Filmstrip band.
            HStack(spacing: 1) {
                ForEach(0..<thumbCount, id: \.self) { _ in
                    ZStack {
                        Rectangle().fill(Color.gray.opacity(0.35))
                        Image(systemName: "photo")
                            .font(.system(size: 9)).foregroundStyle(.white.opacity(0.45))
                    }
                }
            }
            .frame(height: 44)
            .clipped()

            // Title bar (the orange clip label).
            Text(clip.text)
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.white)
                .lineLimit(2)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                .padding(.horizontal, 6).padding(.vertical, 4)
                .background(Color.orange.opacity(0.92))
        }
        .frame(width: width, height: 92)
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6)
            .stroke(isCurrent ? Color.white : Color.white.opacity(0.15),
                    lineWidth: isCurrent ? 2 : 1))
        .overlay(alignment: .topTrailing) {
            Button { onRemove() } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundStyle(.white, .black.opacity(0.5))
            }
            .buttonStyle(.plain)
            .padding(3)
            .help("Remove from timeline")
        }
        .overlay(alignment: .topLeading) { MarkToggle(isMarked: isMarked, onMark: onMark) }
        .contentShape(Rectangle())
        .onTapGesture { onSelect() }
    }
}

/// The merge-selection checkbox shown in a timeline clip's top-left corner.
private struct MarkToggle: View {
    let isMarked: Bool
    let onMark: () -> Void

    var body: some View {
        Button { onMark() } label: {
            Image(systemName: isMarked ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(isMarked ? Color.blue : Color.white.opacity(0.8),
                                 .black.opacity(0.5))
        }
        .buttonStyle(.plain)
        .padding(3)
        .help(isMarked ? "Marked for merge" : "Mark for merge")
    }
}

/// A detected blooper (dead-air) span shown as a clip in the same timeline
/// sequence. Width scales with its real duration; the title bar is red to mark
/// it as dead air, distinct from the orange sentence clips.
private struct BlooperTimelineClip: View {
    let span: BlooperSpan
    let width: CGFloat
    let isCurrent: Bool
    let isMarked: Bool
    let onSelect: () -> Void
    let onMark: () -> Void
    let onRemove: () -> Void

    private var labelText: String {
        switch span.label {
        case "silent": return "silent"
        case "lips_moving": return "lips"
        default: return "no face"
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            // Dead-air band (no thumbnails — there's nothing being said here).
            ZStack {
                Rectangle().fill(Color.gray.opacity(0.22))
                Image(systemName: "waveform.badge.exclamationmark")
                    .font(.system(size: 12)).foregroundStyle(.white.opacity(0.5))
            }
            .frame(height: 44)
            .clipped()

            // Title bar (red = dead air to trim).
            VStack(alignment: .leading, spacing: 1) {
                Text(String(format: "%.1fs", span.duration))
                    .font(.system(size: 10, weight: .semibold))
                Text(labelText)
                    .font(.system(size: 8))
                    .opacity(0.85)
            }
            .foregroundStyle(.white)
            .lineLimit(1)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .padding(.horizontal, 6).padding(.vertical, 4)
            .background(Color.red.opacity(0.7))
        }
        .frame(width: width, height: 92)
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6)
            .stroke(isCurrent ? Color.white : Color.white.opacity(0.15),
                    lineWidth: isCurrent ? 2 : 1))
        .overlay(alignment: .topTrailing) {
            Button { onRemove() } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundStyle(.white, .black.opacity(0.5))
            }
            .buttonStyle(.plain)
            .padding(3)
            .help("Remove this blooper from the timeline")
        }
        .overlay(alignment: .topLeading) { MarkToggle(isMarked: isMarked, onMark: onMark) }
        .contentShape(Rectangle())
        .onTapGesture { onSelect() }
        .help("Blooper: \(span.label) · \(String(format: "%.2fs", span.duration)) — click to preview")
    }
}

/// Time ruler with a tick label every 5 seconds.
private struct TimelineRuler: View {
    let width: CGFloat
    let totalSeconds: Int
    let pxPerSec: CGFloat

    private func stamp(_ s: Int) -> String { String(format: "0:%02d", s) }

    var body: some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array(stride(from: 0, through: max(totalSeconds, 5), by: 5)), id: \.self) { s in
                VStack(alignment: .leading, spacing: 2) {
                    Rectangle().fill(.white.opacity(0.3)).frame(width: 1, height: 6)
                    Text(stamp(s)).font(.system(size: 8)).foregroundStyle(.white.opacity(0.5))
                }
                .offset(x: CGFloat(s) * pxPerSec)
            }
        }
        .frame(width: max(width, CGFloat(totalSeconds) * pxPerSec), height: 18, alignment: .topLeading)
    }
}

/// Decorative audio waveform track (no real audio yet).
private struct WaveformTrack: View {
    let width: CGFloat

    private var barCount: Int { max(1, Int(width / 4)) }
    private func barHeight(_ i: Int) -> CGFloat {
        let h = (sin(Double(i) * 0.7) + sin(Double(i) * 0.23)) / 2  // -1...1-ish
        return 5 + CGFloat((h + 1) / 2) * 26
    }

    var body: some View {
        HStack(spacing: 2) {
            ForEach(0..<barCount, id: \.self) { i in
                Capsule().fill(Color.blue.opacity(0.75))
                    .frame(width: 2, height: barHeight(i))
            }
        }
        .frame(width: width, height: 40, alignment: .leading)
        .padding(.horizontal, 4)
        .background(Color.blue.opacity(0.12), in: RoundedRectangle(cornerRadius: 4))
    }
}

// MARK: - Merge preview page

/// A clip chosen for merging — a sentence (text-only) or a blooper (video span).
private enum MergeClip: Identifiable {
    case sentence(LineScore)
    case blooper(BlooperSpan)

    var id: String {
        switch self {
        case .sentence(let c): return "s\(c.id)"
        case .blooper(let b): return "b\(b.id)"
        }
    }

    var seconds: Double {
        switch self {
        case .sentence(let c): return Double(estimatedSeconds(c.text))
        case .blooper(let b): return b.duration
        }
    }
}

/// "Next page" after picking clips to merge: concatenates the marked blooper
/// spans into one composition and plays it; sentence clips appear in the
/// storyboard but have no footage to composite yet.
private struct MergePreviewView: View {
    let items: [MergeClip]
    let videoURL: URL?
    let onBack: () -> Void

    @State private var player = AVPlayer()
    @State private var hasVideo = false
    @State private var buildError: String?

    private var totalSeconds: Int { Int(items.reduce(0.0) { $0 + $1.seconds }.rounded()) }
    private var sentenceCount: Int {
        items.filter { if case .sentence = $0 { return true }; return false }.count
    }

    var body: some View {
        VStack(spacing: 0) {
            topBar
            Divider()
            preview.padding(24)
            Spacer(minLength: 0)
            Divider()
            storyboard
        }
        .onAppear(perform: build)
        .onDisappear { player.pause() }
    }

    private var topBar: some View {
        HStack {
            Button { onBack() } label: { Label("Back", systemImage: "chevron.left") }
                .buttonStyle(.borderless)
            Spacer()
            VStack(spacing: 1) {
                Text("Merged preview").font(.headline)
                Text("\(items.count) clip\(items.count == 1 ? "" : "s") · total ≈ \(totalSeconds)s")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button { } label: { Label("Export", systemImage: "square.and.arrow.up") }
                .disabled(true)
                .help("Export the merged Short — coming next")
        }
        .padding()
    }

    @ViewBuilder
    private var preview: some View {
        if hasVideo {
            VideoPlayer(player: player)
                .background(Color.black)
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .frame(maxWidth: .infinity, minHeight: 320)
                .overlay(alignment: .bottomLeading) {
                    if sentenceCount > 0 {
                        Text("\(sentenceCount) text-only clip\(sentenceCount == 1 ? "" : "s") not shown — no footage yet")
                            .font(.caption2).foregroundStyle(.white)
                            .padding(.horizontal, 8).padding(.vertical, 4)
                            .background(.black.opacity(0.55), in: Capsule())
                            .padding(10)
                    }
                }
        } else {
            ZStack {
                RoundedRectangle(cornerRadius: 14).fill(Color(nsColor: .controlBackgroundColor))
                RoundedRectangle(cornerRadius: 14)
                    .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
                    .foregroundStyle(.quaternary)
                VStack(spacing: 10) {
                    Image(systemName: "rectangle.stack.badge.play")
                        .font(.system(size: 46)).foregroundStyle(.secondary)
                    Text(buildError ?? "Nothing to play — the merged clips are text-only (no footage yet).")
                        .font(.callout).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center).frame(maxWidth: 460)
                }
                .padding(32)
            }
            .frame(maxWidth: .infinity, minHeight: 320)
        }
    }

    private var storyboard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Sequence").font(.callout.weight(.semibold)).foregroundStyle(.white)
                .padding(.horizontal, 20).padding(.top, 12)
            ScrollView(.horizontal, showsIndicators: true) {
                HStack(spacing: 8) {
                    ForEach(Array(items.enumerated()), id: \.element.id) { idx, item in
                        MergeCard(index: idx + 1, item: item)
                    }
                }
                .padding(.horizontal, 20).padding(.bottom, 16)
            }
        }
        .background(Color(white: 0.12))
    }

    /// Concatenate the marked blooper spans (video+audio) into one composition.
    private func build() {
        guard let videoURL else { return }
        let asset = AVURLAsset(url: videoURL)
        let comp = AVMutableComposition()
        var cursor = CMTime.zero
        do {
            for item in items {
                if case let .blooper(b) = item {
                    let range = CMTimeRange(
                        start: CMTime(seconds: b.start, preferredTimescale: 600),
                        end: CMTime(seconds: b.end, preferredTimescale: 600))
                    try comp.insertTimeRange(range, of: asset, at: cursor)
                    cursor = cursor + range.duration
                }
            }
            if cursor > .zero {
                player.replaceCurrentItem(with: AVPlayerItem(asset: comp))
                hasVideo = true
                player.seek(to: .zero)
                player.play()
            }
        } catch {
            buildError = "Couldn't build the merge: \(error.localizedDescription)"
        }
    }
}

/// One card in the merged-sequence storyboard.
private struct MergeCard: View {
    let index: Int
    let item: MergeClip

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ZStack {
                Rectangle().fill(color.opacity(0.3))
                Image(systemName: icon).font(.system(size: 16)).foregroundStyle(.white.opacity(0.7))
            }
            .frame(height: 50)
            VStack(alignment: .leading, spacing: 2) {
                Text("#\(index) · \(title)").font(.system(size: 9, weight: .semibold))
                Text(subtitle).font(.system(size: 8)).opacity(0.85)
            }
            .foregroundStyle(.white).lineLimit(2)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 6).padding(.vertical, 4)
            .background(color)
        }
        .frame(width: 130, height: 92)
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private var color: Color {
        if case .blooper = item { return .red.opacity(0.7) } else { return .orange.opacity(0.92) }
    }
    private var icon: String {
        if case .blooper = item { return "waveform.badge.exclamationmark" } else { return "text.alignleft" }
    }
    private var title: String {
        if case .blooper = item { return "blooper" } else { return "clip" }
    }
    private var subtitle: String {
        switch item {
        case .sentence(let c): return c.text
        case .blooper(let b): return String(format: "%.2fs · %@", b.duration, b.label)
        }
    }
}

#Preview {
    ContentView()
}
