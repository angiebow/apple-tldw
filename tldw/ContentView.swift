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
import UniformTypeIdentifiers

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
    @State private var isDropTargeted = false
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
                        sourceVideoURL: model.sourceVideoURL,
                        transcriptSegments: model.transcriptSegments,
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
                           subtitle: "Drop in a recording — tldw extracts the audio, transcribes it, then finds the most relevant and viral-worthy lines.")
                Spacer()
                HStack(spacing: 10) {
                    serverPill
                    themeToggle
                }
            }

            titleField

            mediaDropZone

            if !model.statusMessage.isEmpty {
                Label(model.statusMessage,
                      systemImage: model.serverReachable == false
                        ? "exclamationmark.triangle.fill" : "info.circle")
                    .font(.callout)
                    .foregroundStyle(model.serverReachable == false ? Color.red : Color.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
            }
        }
        .padding(28)
    }

    /// Optional title for the recording. Fed to the backend so the summary — and
    /// the relevance ranking derived from it — stays anchored to the video's topic.
    private var titleField: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Video title")
                .font(.callout.weight(.medium))
                .foregroundStyle(.secondary)
            TextField("e.g. \"How we shipped Shorts in a weekend\" (optional)",
                      text: $model.videoTitle)
                .textFieldStyle(.plain)
                .font(.title3)
                .padding(.horizontal, 14).padding(.vertical, 11)
                .background(Color(nsColor: .textBackgroundColor),
                            in: RoundedRectangle(cornerRadius: 10))
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .strokeBorder(Color.secondary.opacity(0.25), lineWidth: 1)
                )
            Text("Helps tldw summarize the transcript around what the video is about.")
                .font(.caption).foregroundStyle(.tertiary)
        }
    }

    /// The input is now a recording, not pasted text: drag an audio/video file
    /// here (or click to browse) and the whole pipeline runs from the extracted audio.
    private var mediaDropZone: some View {
        Button { pickAndTranscribe() } label: {
            ZStack {
                RoundedRectangle(cornerRadius: 16)
                    .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [7]))
                    .foregroundStyle(isDropTargeted ? Color.accentColor : Color.secondary.opacity(0.45))
                VStack(spacing: 10) {
                    Image(systemName: "waveform.badge.plus")
                        .font(.system(size: 40))
                        .foregroundStyle(isDropTargeted ? Color.accentColor : .secondary)
                    Text(isDropTargeted ? "Drop to transcribe" : "Drag an audio or video recording here")
                        .font(.title3.weight(.medium))
                    Text("or click to browse — the extracted audio is transcribed, then ranked into Shorts")
                        .font(.callout).foregroundStyle(.secondary)
                    Text("MP4 · MOV · M4A · MP3 · WAV · and more")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
                .padding(24)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(isDropTargeted ? Color.accentColor.opacity(0.08) : Color.clear,
                        in: RoundedRectangle(cornerRadius: 16))
            .contentShape(RoundedRectangle(cornerRadius: 16))
        }
        .buttonStyle(.plain)
        .dropDestination(for: URL.self) { urls, _ in handleDrop(urls) }
            isTargeted: { isDropTargeted = $0 }
    }

    /// Browse for a local video/audio file, then run the transcribe→rank pipeline.
    private func pickAndTranscribe() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.movie, .video, .mpeg4Movie, .quickTimeMovie, .audio, .mp3, .wav, .mpeg4Audio]
        panel.message = "Choose a video or audio recording to turn into Shorts."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        start(url)
    }

    /// Accept a dropped file, ignoring anything that isn't audio/video.
    private func handleDrop(_ urls: [URL]) -> Bool {
        guard let url = urls.first(where: isMedia) else { return false }
        start(url)
        return true
    }

    private func isMedia(_ url: URL) -> Bool {
        if let type = UTType(filenameExtension: url.pathExtension) {
            return type.conforms(to: .audiovisualContent)
                || type.conforms(to: .movie)
                || type.conforms(to: .audio)
        }
        return ["mov", "mp4", "m4v", "avi", "mkv", "webm",
                "wav", "mp3", "m4a", "aac", "flac", "ogg"].contains(url.pathExtension.lowercased())
    }

    private func start(_ url: URL) {
        // Dropped/panel files carry a sandbox extension; hold it open for the read.
        _ = url.startAccessingSecurityScopedResource()
        Task { await model.transcribeAndHighlight(at: url) }
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
                if shownLines.isEmpty {
                    emptyResults
                } else {
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
        }
        .sheet(item: $detailLine) { line in
            ShortDetailView(line: line, category: category)
        }
    }

    /// Shown in the grid when the current category has nothing to show — either
    /// none were detected, or the search filtered everything out.
    private var emptyResults: some View {
        let searching = !searchText.isEmpty
        let noun = category == .viral ? "viral" : "relevant"
        return VStack(spacing: 12) {
            Image(systemName: searching ? "magnifyingglass" : "sparkles")
                .font(.system(size: 42)).foregroundStyle(.secondary)
            Text(searching ? "No \(noun) lines match your search"
                           : "No \(noun) lines detected")
                .font(.title3.weight(.medium))
            Text(searching
                 ? "Try a different search term, or clear the search."
                 : "Nothing in this recording crossed the \(noun) threshold — try the other tab or another recording.")
                .font(.callout).foregroundStyle(.secondary)
                .multilineTextAlignment(.center).frame(maxWidth: 420)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 80)
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

            Button { model.reset() } label: {
                Label("New recording", systemImage: "arrow.uturn.backward")
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

/// Output orientation chosen on the editor page for exported / merged Shorts.
enum ShortOrientation: String, CaseIterable, Identifiable {
    case portrait  = "Portrait"
    case landscape = "Landscape"

    var id: String { rawValue }
    /// Backend `vertical` flag: portrait → 1080×1920 with a blurred top/bottom
    /// fill of the clip; landscape → the original framing, untouched.
    var vertical: Bool { self == .portrait }
    var systemImage: String { self == .portrait ? "rectangle.portrait" : "rectangle" }
    var exportHelp: String {
        self == .portrait
            ? "Portrait 1080×1920 — top/bottom are a blurred extension of the clip."
            : "Landscape — keep the recording's original orientation and framing."
    }
}

private struct EditorView: View {
    let clips: [LineScore]                 // selected lines, in arranged order
    let sourceVideoURL: URL?               // the recording clips are cut from
    let transcriptSegments: [TranscriptSegment]  // word times → karaoke captions
    let onBack: () -> Void
    let onRemove: (Int) -> Void            // by line id
    let onMove: (Int, Int) -> Void         // (fromOffset, toOffset) — live drag reorder

    // Export state: cut the selected spans to .mp4 files via the backend.
    @State private var isExporting = false
    @State private var exportError: String?
    @State private var exportNotice: String?
    /// Landscape (as-is) vs portrait (blurred top/bottom fill) for exports + merge.
    @State private var orientation: ShortOrientation = .portrait
    /// Burn karaoke captions into exports (and show them live in the preview).
    @State private var captionsOn = true

    /// What the big preview is showing — a sentence clip (text) or a blooper (video).
    private enum Selection: Hashable {
        case sentence(Int)   // LineScore id
        case blooper(Int)    // BlooperSpan id
    }
    @State private var selection: Selection?
    @State private var marked: Set<Selection> = []   // clips ticked for merging
    @State private var showMergePreview = false
    /// Transient note shown when a merge-mark switches kinds (bloopers ↔ transcript).
    @State private var mergeModeNote: String?
    /// The transcript clip being interactively dragged, and the live pointer x
    /// (in the clip-strip coordinate space) driving its follow-the-cursor offset.
    @State private var draggingID: Int?
    @State private var dragX: CGFloat = 0
    /// Same, for the blooper lane below (reordered independently).
    @State private var draggingBlooperID: Int?
    @State private var bloopDragX: CGFloat = 0

    // Detected blooper (dead-air) spans, surfaced into the same timeline sequence.
    @State private var bloopers: [BlooperSpan] = []
    @State private var blooperVideoURL: URL?
    @State private var bigPlayer = AVPlayer()       // drives the top preview (sentence clips + blooper spans)
    @State private var bigEndObserver: Any?
    @State private var loadedURL: URL?              // which file bigPlayer currently holds

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
    /// Per-clip bed volume (0–1), and the looping player that plays the bed in
    /// sync with the clip preview.
    @State private var bedVolumeByClip: [Int: Double] = [:]
    @State private var bedPlayer: AVAudioPlayer?
    private let service = HighlightService()

    private static let defaultBedVolume = 0.35
    private func bedVolume(_ id: Int) -> Double { bedVolumeByClip[id] ?? Self.defaultBedVolume }

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
                if bedVolumeByClip[clip.id] == nil { bedVolumeByClip[clip.id] = Self.defaultBedVolume }
                // If this clip is the one on screen, start its bed right away.
                if selection == .sentence(clip.id) { startBed(for: clip) }
            } catch {
                bedError = error.localizedDescription
            }
        }
    }

    /// Loop the clip's generated bed under the preview at its chosen volume.
    private func startBed(for clip: LineScore) {
        stopBed()
        guard let b64 = bedByClip[clip.id]?.audioB64,
              let data = Data(base64Encoded: b64) else { return }
        do {
            let p = try AVAudioPlayer(data: data)
            p.numberOfLoops = -1               // loop the short bed to cover the clip
            p.volume = Float(bedVolume(clip.id))
            p.play()
            bedPlayer = p
        } catch {
            bedError = "Couldn't play the music bed: \(error.localizedDescription)"
        }
    }

    private func stopBed() {
        bedPlayer?.stop()
        bedPlayer = nil
    }

    /// Live-updating binding for a clip's bed volume slider.
    private func bedVolumeBinding(_ id: Int) -> Binding<Double> {
        Binding(
            get: { bedVolume(id) },
            set: { v in
                bedVolumeByClip[id] = v
                if selection == .sentence(id) { bedPlayer?.volume = Float(v) }
            }
        )
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

    /// Point the shared preview player at `url` (no-op if it's already loaded).
    /// The source recording and the blooper scan share the same file, so this
    /// usually loads once and both sentence + blooper previews reuse it.
    private func load(_ url: URL?) {
        guard let url, loadedURL != url else { return }
        bigPlayer.replaceCurrentItem(with: AVPlayerItem(url: url))
        loadedURL = url
    }

    /// Seek the big preview to [start, end] and play only that range.
    private func playSpan(start: Double, end: Double) {
        guard let item = bigPlayer.currentItem else { return }
        stopSpan()
        let startTime = CMTime(seconds: start, preferredTimescale: 600)
        let endTime = CMTime(seconds: end, preferredTimescale: 600)
        bigPlayer.pause()
        item.seek(to: startTime, toleranceBefore: .zero, toleranceAfter: .zero) { _ in
            bigPlayer.play()
        }
        bigEndObserver = bigPlayer.addBoundaryTimeObserver(
            forTimes: [NSValue(time: endTime)], queue: .main
        ) { [weak bigPlayer] in bigPlayer?.pause() }
    }

    /// Select a transcript line and play its recorded span from the source video.
    /// Falls back to a text placeholder for pasted lines that have no timecodes.
    private func selectSentence(_ clip: LineScore) {
        selection = .sentence(clip.id)
        if let s = clip.start, let e = clip.end, e > s, sourceVideoURL != nil {
            load(sourceVideoURL)
            playSpan(start: s, end: e)
            startBed(for: clip)   // plays only if this clip has a generated bed
        } else {
            stopSpan()
        }
    }

    /// Select a blooper clip and play its span in the big preview.
    private func selectBlooper(_ span: BlooperSpan) {
        selection = .blooper(span.id)
        load(blooperVideoURL)
        guard bigPlayer.currentItem != nil else { return }
        playSpan(start: span.start, end: span.end)
    }

    private func stopSpan() {
        bigPlayer.pause()
        stopBed()
        if let bigEndObserver { bigPlayer.removeTimeObserver(bigEndObserver); self.bigEndObserver = nil }
    }

    /// Receive detected spans (and their source video) from the panel.
    private func receiveBloopers(_ spans: [BlooperSpan], _ url: URL?) {
        bloopers = spans
        blooperVideoURL = url
        load(url)
        // If the selected blooper vanished after a re-scan, fall back to a sentence.
        if case let .blooper(id) = selection, !spans.contains(where: { $0.id == id }) {
            selection = clips.first.map { .sentence($0.id) }
            stopSpan()
        }
    }

    /// Real duration of a line's recorded span; falls back to a text estimate for
    /// pasted lines that carry no timecodes.
    private func clipSeconds(_ c: LineScore) -> Double {
        if let s = c.start, let e = c.end, e > s { return e - s }
        return Double(estimatedSeconds(c.text))
    }

    /// Total length of the sentence clips (shown in the header).
    private var totalSeconds: Int {
        Int(clips.reduce(0.0) { $0 + clipSeconds($1) }.rounded())
    }

    /// Timeline length driving the ruler — the longer of the two parallel lanes
    /// (transcript clips vs. the blooper lane below it).
    private var timelineSeconds: Int {
        max(totalSeconds, Int(bloopers.reduce(0.0) { $0 + $1.duration }.rounded()))
    }

    var body: some View {
        if showMergePreview {
            MergePreviewView(items: mergeItems, videoURL: sourceVideoURL ?? blooperVideoURL,
                             segments: transcriptSegments, orientation: orientation,
                             captionsOn: captionsOn, beds: bedByClip, bedVolumes: bedVolumeByClip,
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
            // The panel auto-scans the first-page recording (no second video pick).
            BlooperPanel(onBloopers: receiveBloopers, sourceVideoURL: sourceVideoURL)
        }
        .onAppear {
            if selection == nil, let f = clips.first { selection = .sentence(f.id) }
            // Load the source recording and park on the first clip's start frame,
            // so the preview shows real footage without auto-playing on entry.
            load(sourceVideoURL)
            if let s = clips.first?.start {
                bigPlayer.seek(to: CMTime(seconds: s, preferredTimescale: 600))
            }
        }
        .onDisappear { stopSpan() }
        .alert("Couldn’t export clips",
               isPresented: Binding(get: { exportError != nil },
                                    set: { if !$0 { exportError = nil } })) {
            Button("OK", role: .cancel) { exportError = nil }
        } message: {
            Text(exportError ?? "")
        }
        .alert("Clips exported",
               isPresented: Binding(get: { exportNotice != nil },
                                    set: { if !$0 { exportNotice = nil } })) {
            Button("OK", role: .cancel) { exportNotice = nil }
        } message: {
            Text(exportNotice ?? "")
        }
    }

    private func isSentence(_ s: Selection) -> Bool {
        if case .sentence = s { return true }; return false
    }

    /// Noun for the Merge button — reflects the single kind currently marked.
    private var markedKindLabel: String {
        marked.contains(where: { !isSentence($0) }) ? "blooper" : "clip"
    }

    /// Toggle a clip's merge mark. A merge can only contain one kind — transcript
    /// clips *or* bloopers (each with their backsong) — never both, so marking the
    /// opposite kind clears the current selection and switches mode.
    private func toggleMark(_ key: Selection) {
        if marked.contains(key) {
            marked.remove(key)
            return
        }
        let addingSentence = isSentence(key)
        if marked.contains(where: { isSentence($0) != addingSentence }) {
            marked = marked.filter { isSentence($0) == addingSentence }
            flashMergeNote(addingSentence
                ? "Switched to transcript clips — they can’t be merged with bloopers."
                : "Switched to bloopers — they can’t be merged with transcript clips.")
        }
        marked.insert(key)
    }

    /// Briefly surface why the merge selection changed kinds.
    private func flashMergeNote(_ text: String) {
        mergeModeNote = text
        Task {
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            mergeModeNote = nil
        }
    }

    /// Name of the coordinate space the clip drag reads its pointer x from.
    private static let clipStripSpace = "clipStrip"
    /// Horizontal gap between timeline clips (matches the HStack spacing).
    private static let clipGap: CGFloat = 2

    /// Center x of the clip at `index` in the current transcript-lane layout.
    private func clipCenterX(_ index: Int) -> CGFloat {
        var x: CGFloat = 0
        for i in 0..<index { x += clipWidth(clips[i]) + Self.clipGap }
        return x + clipWidth(clips[index]) / 2
    }

    /// Which slot the pointer at `x` (clip-strip space) currently hovers over —
    /// the index whose half-way point the pointer has crossed.
    private func dragTargetIndex(x: CGFloat) -> Int {
        var acc: CGFloat = 0
        for (i, c) in clips.enumerated() {
            let w = clipWidth(c)
            if x < acc + w / 2 { return i }
            acc += w + Self.clipGap
        }
        return max(0, clips.count - 1)
    }

    /// Interactive drag: the clip tracks the cursor, and as its pointer crosses a
    /// neighbour's midpoint the sequence reorders live (a small `minimumDistance`
    /// keeps a plain click working as a select rather than a drag).
    private func clipDrag(_ clip: LineScore) -> some Gesture {
        DragGesture(minimumDistance: 6, coordinateSpace: .named(Self.clipStripSpace))
            .onChanged { value in
                if draggingID == nil { draggingID = clip.id }
                guard draggingID == clip.id else { return }
                dragX = value.location.x
                let target = dragTargetIndex(x: dragX)
                if let cur = clips.firstIndex(where: { $0.id == clip.id }), target != cur {
                    withAnimation(.easeInOut(duration: 0.18)) {
                        onMove(cur, target > cur ? target + 1 : target)
                    }
                }
            }
            .onEnded { _ in draggingID = nil }
    }

    /// Name of the coordinate space the blooper drag reads its pointer x from.
    private static let bloopStripSpace = "bloopStrip"

    /// Center x of the blooper at `index` in the current blooper-lane layout.
    private func bloopCenterX(_ index: Int) -> CGFloat {
        var x: CGFloat = 0
        for i in 0..<index { x += bloopWidth(bloopers[i]) + Self.clipGap }
        return x + bloopWidth(bloopers[index]) / 2
    }

    /// Which blooper slot the pointer at `x` (blooper-strip space) hovers over.
    private func bloopTargetIndex(x: CGFloat) -> Int {
        var acc: CGFloat = 0
        for (i, b) in bloopers.enumerated() {
            let w = bloopWidth(b)
            if x < acc + w / 2 { return i }
            acc += w + Self.clipGap
        }
        return max(0, bloopers.count - 1)
    }

    /// Interactive drag for the blooper lane — mirrors `clipDrag`, but reorders the
    /// local `bloopers` state directly (no parent binding needed).
    private func blooperDrag(_ span: BlooperSpan) -> some Gesture {
        DragGesture(minimumDistance: 6, coordinateSpace: .named(Self.bloopStripSpace))
            .onChanged { value in
                if draggingBlooperID == nil { draggingBlooperID = span.id }
                guard draggingBlooperID == span.id else { return }
                bloopDragX = value.location.x
                let target = bloopTargetIndex(x: bloopDragX)
                if let cur = bloopers.firstIndex(where: { $0.id == span.id }), target != cur {
                    withAnimation(.easeInOut(duration: 0.18)) {
                        bloopers.move(fromOffsets: IndexSet(integer: cur),
                                      toOffset: target > cur ? target + 1 : target)
                    }
                }
            }
            .onEnded { _ in draggingBlooperID = nil }
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
            Toggle(isOn: $captionsOn) { Label("Captions", systemImage: "captions.bubble") }
                .toggleStyle(.button)
                .help("Burn word-by-word karaoke captions into the export (needs ffmpeg libass), and preview them live")
            orientationPicker
            if isExporting {
                ProgressView().controlSize(.small)
            }
            Button { exportClips() } label: {
                Label("Export \(exportableClips.count) clip\(exportableClips.count == 1 ? "" : "s")",
                      systemImage: "square.and.arrow.down")
            }
            .disabled(isExporting || sourceVideoURL == nil || exportableClips.isEmpty)
            .help(exportHelp)
        }
        .padding()
    }

    /// Landscape / portrait segmented control that drives every export on this page.
    private var orientationPicker: some View {
        Picker("Orientation", selection: $orientation) {
            ForEach(ShortOrientation.allCases) { o in
                Label(o.rawValue, systemImage: o.systemImage).tag(o)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .fixedSize()
        .help(orientation.exportHelp)
    }

    /// Selected clips that have a source-video span to cut (timestamped lines only).
    private var exportableClips: [LineScore] {
        clips.filter { $0.start != nil && $0.end != nil }
    }

    private var exportHelp: String {
        if sourceVideoURL == nil { return "No source recording to cut from." }
        if exportableClips.isEmpty { return "These clips have no timecodes to cut." }
        return "Cut the selected spans to .mp4 files and reveal them in Finder. "
            + orientation.exportHelp
    }

    /// Cut the exportable clips out of the source video, then reveal them in Finder.
    private func exportClips() {
        guard let url = sourceVideoURL else { return }
        let spans = exportableClips.map { c in
            ClipSpan(start: c.start ?? 0, end: c.end ?? 0, text: c.text,
                     musicB64: bedByClip[c.id]?.audioB64,
                     musicVolume: bedByClip[c.id] != nil ? bedVolume(c.id) : nil)
        }
        isExporting = true
        exportError = nil
        exportNotice = nil
        Task {
            defer { isExporting = false }
            do {
                // Landscape (as-is) or portrait (blurred fill); + karaoke captions.
                let result = try await service.exportClips(
                    videoPath: url.path, clips: spans, segments: transcriptSegments,
                    vertical: orientation.vertical, subtitles: captionsOn)
                // Reveal the written files (or the folder) in Finder.
                let urls = result.clips.map { URL(fileURLWithPath: $0.path) }
                NSWorkspace.shared.activateFileViewerSelecting(
                    urls.isEmpty ? [URL(fileURLWithPath: result.outputDir)] : urls)
                // Captions were asked for but this ffmpeg can't burn them (no libass).
                if result.subtitlesRequested && !result.subtitlesApplied {
                    exportNotice = "Exported \(result.count) \(orientation.rawValue.lowercased()) "
                        + "clip\(result.count == 1 ? "" : "s"), but captions were skipped — this ffmpeg "
                        + "has no subtitles support (install an ffmpeg built with libass to burn karaoke subtitles)."
                }
            } catch {
                exportError = error.localizedDescription
            }
        }
    }

    @ViewBuilder
    private var previewArea: some View {
        Group {
            if let span = currentBlooper, blooperVideoURL != nil {
                blooperPreview(span)
            } else if let clip = currentClip,
                      clip.start != nil, clip.end != nil, sourceVideoURL != nil {
                sentenceVideoPreview(clip)
            } else {
                sentencePreview
            }
        }
        // Frame the preview to the chosen output shape so the orientation toggle is
        // visible here: portrait shows the 9:16 canvas (real footage letterboxed —
        // the blurred top/bottom fill is added by ffmpeg on export), landscape 16:9.
        .aspectRatio(orientation == .portrait ? 9.0 / 16.0 : 16.0 / 9.0, contentMode: .fit)
        .frame(maxWidth: .infinity, maxHeight: orientation == .portrait ? 480 : 340)
        .overlay(alignment: .top) {
            if orientation == .portrait {
                Text("Portrait 9:16 · blurred fill")
                    .font(.caption2).foregroundStyle(.white)
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(.black.opacity(0.55), in: Capsule())
                    .padding(8)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: orientation)
    }

    /// A selected transcript line's real footage, cut to its recorded [start, end]
    /// span and played in place from the source recording.
    /// Transcript words that fall inside a clip's span (source timeline) — feeds
    /// the live caption overlay so it matches the burned-in export captions.
    private func wordsFor(_ clip: LineScore) -> [TranscriptWord] {
        guard let s = clip.start, let e = clip.end else { return [] }
        return transcriptSegments.flatMap { $0.words ?? [] }
            .filter { $0.start < e && $0.end > s }
    }

    private func sentenceVideoPreview(_ clip: LineScore) -> some View {
        ZStack(alignment: .bottom) {
            OrientationVideoCanvas(player: bigPlayer, portrait: orientation == .portrait)

            if captionsOn {
                CaptionOverlay(player: bigPlayer, words: wordsFor(clip),
                               vertical: orientation == .portrait)
            }

            HStack(spacing: 8) {
                Image(systemName: "text.quote")
                Text("“\(clip.text)”").lineLimit(1)
                Spacer()
                if let s = clip.start, let e = clip.end {
                    Text("\(timecode(s)) – \(timecode(e)) · \(String(format: "%.1fs", e - s))")
                        .monospacedDigit()
                }
                Button { selectSentence(clip) } label: { Label("Replay", systemImage: "arrow.clockwise") }
                    .buttonStyle(.borderless)
            }
            .font(.caption.weight(.medium))
            .foregroundStyle(.white)
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background(.black.opacity(0.55))
        }
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    /// m:ss timecode for a position in the source video.
    private func timecode(_ s: Double) -> String {
        let t = Int(s.rounded())
        return String(format: "%d:%02d", t / 60, t % 60)
    }

    /// The blooper span playing in place from its source video.
    private func blooperPreview(_ span: BlooperSpan) -> some View {
        ZStack(alignment: .bottom) {
            OrientationVideoCanvas(player: bigPlayer, portrait: orientation == .portrait)

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

    /// Text placeholder for a selected line with no source span to play — pasted
    /// transcripts (no timecodes) or when the source recording is unavailable.
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
                Text("This line has no source timecodes — drop in a recording to preview real footage")
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

                if bed != nil {
                    HStack(spacing: 6) {
                        Image(systemName: "speaker.wave.2.fill")
                            .font(.caption).foregroundStyle(.secondary)
                        Slider(value: bedVolumeBinding(clip.id), in: 0...1)
                            .frame(width: 120)
                        Text("\(Int((bedVolume(clip.id) * 100).rounded()))%")
                            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                            .frame(width: 36, alignment: .trailing)
                    }
                    .help("How loud the music bed sits under the clip — applies to both the preview and the export")
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
                        Label("Bed only", systemImage: "play.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .help("Audition just the music bed")
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
        max(90, CGFloat(clipSeconds(c)) * pxPerSec)
    }
    private func bloopWidth(_ b: BlooperSpan) -> CGFloat {
        max(54, CGFloat(b.duration) * pxPerSec)
    }
    /// Width of the transcript lane (sentence clips laid end to end).
    private var transcriptWidth: CGFloat {
        let sentences = clips.reduce(0) { $0 + clipWidth($1) }
        return sentences + CGFloat(max(0, clips.count - 1)) * 2
    }
    /// Width of the blooper lane below the transcript lane.
    private var blooperLaneWidth: CGFloat {
        let dead = bloopers.reduce(0) { $0 + bloopWidth($1) }
        return dead + CGFloat(max(0, bloopers.count - 1)) * 2
    }
    /// Ruler/waveform span — the wider of the two parallel lanes.
    private var contentWidth: CGFloat {
        max(transcriptWidth, blooperLaneWidth)
    }

    private var strip: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Text("Timeline").font(.callout.weight(.semibold)).foregroundStyle(.white)
                if let note = mergeModeNote {
                    Text(note)
                        .font(.caption2).foregroundStyle(.yellow)
                        .lineLimit(1).transition(.opacity)
                }
                Spacer()
                if !marked.isEmpty {
                    Button { showMergePreview = true } label: {
                        Label("Merge \(marked.count) \(markedKindLabel)\(marked.count == 1 ? "" : "s")",
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
                    // Transcript lane — the sentence clips.
                    LaneLabel(text: "Transcript")
                    HStack(spacing: EditorView.clipGap) {
                        ForEach(Array(clips.enumerated()), id: \.element.id) { index, clip in
                            TimelineClip(clip: clip,
                                         width: clipWidth(clip),
                                         isCurrent: selection == .sentence(clip.id),
                                         isMarked: marked.contains(.sentence(clip.id)),
                                         onSelect: { selectSentence(clip) },
                                         onMark: { toggleMark(.sentence(clip.id)) },
                                         onRemove: { onRemove(clip.id) })
                                // Interactive reorder: the dragged clip follows the
                                // cursor while the others shift live around it.
                                .offset(x: draggingID == clip.id ? dragX - clipCenterX(index) : 0)
                                .scaleEffect(draggingID == clip.id ? 1.06 : 1)
                                .shadow(color: .black.opacity(draggingID == clip.id ? 0.4 : 0),
                                        radius: 6, y: 3)
                                .zIndex(draggingID == clip.id ? 1 : 0)
                                .gesture(clipDrag(clip))
                        }
                    }
                    .coordinateSpace(name: EditorView.clipStripSpace)
                    .animation(.easeInOut(duration: 0.18), value: clips.map(\.id))
                    // Blooper lane — separate sequence below the transcript clips.
                    if !bloopers.isEmpty {
                        LaneLabel(text: "Bloopers")
                        HStack(spacing: EditorView.clipGap) {
                            ForEach(Array(bloopers.enumerated()), id: \.element.id) { index, span in
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
                                    // Interactive reorder, matching the transcript lane.
                                    .offset(x: draggingBlooperID == span.id ? bloopDragX - bloopCenterX(index) : 0)
                                    .scaleEffect(draggingBlooperID == span.id ? 1.06 : 1)
                                    .shadow(color: .black.opacity(draggingBlooperID == span.id ? 0.4 : 0),
                                            radius: 6, y: 3)
                                    .zIndex(draggingBlooperID == span.id ? 1 : 0)
                                    .gesture(blooperDrag(span))
                            }
                        }
                        .coordinateSpace(name: EditorView.bloopStripSpace)
                        .animation(.easeInOut(duration: 0.18), value: bloopers.map(\.id))
                    }
                    // Backsound lane — generated music beds, each tile aligned
                    // under the clip it belongs to (blank gap where a clip has none).
                    if !bedByClip.isEmpty {
                        LaneLabel(text: "Backsound")
                        HStack(spacing: EditorView.clipGap) {
                            ForEach(clips) { clip in
                                if let bed = bedByClip[clip.id] {
                                    BacksoundTimelineClip(
                                        bed: bed,
                                        volume: bedVolume(clip.id),
                                        width: clipWidth(clip),
                                        isCurrent: selection == .sentence(clip.id),
                                        onSelect: { selectSentence(clip) },
                                        onRemove: {
                                            bedByClip[clip.id] = nil
                                            bedVolumeByClip[clip.id] = nil
                                            if selection == .sentence(clip.id) { stopBed() }
                                        })
                                } else {
                                    Color.clear.frame(width: clipWidth(clip), height: 44)
                                }
                            }
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

/// A generated music bed shown as a tile in the Backsound lane, sized to match
/// the clip it scores and showing its mood + volume. Tapping selects the clip.
private struct BacksoundTimelineClip: View {
    let bed: BacksoundResponse
    let volume: Double
    let width: CGFloat
    let isCurrent: Bool
    let onSelect: () -> Void
    let onRemove: () -> Void

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: "music.note").font(.system(size: 11, weight: .bold))
            VStack(alignment: .leading, spacing: 1) {
                Text(bed.emotion).font(.system(size: 9, weight: .semibold)).lineLimit(1)
                Text("\(Int((volume * 100).rounded()))% · \(String(format: "%.0fs", bed.durationS))")
                    .font(.system(size: 8)).opacity(0.85).lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 6)
        .frame(width: width, height: 44, alignment: .leading)
        .background(Color.purple.opacity(0.85), in: RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6)
            .stroke(isCurrent ? Color.white : Color.white.opacity(0.15),
                    lineWidth: isCurrent ? 2 : 1))
        .overlay(alignment: .topTrailing) {
            Button { onRemove() } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundStyle(.white, .black.opacity(0.5))
            }
            .buttonStyle(.plain).padding(2)
            .help("Remove this music bed")
        }
        .contentShape(Rectangle())
        .onTapGesture { onSelect() }
        .help("\(bed.emotion) bed · \(Int((volume * 100).rounded()))% — tap to select this clip")
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

/// Small caption marking a timeline lane (Transcript / Bloopers).
private struct LaneLabel: View {
    let text: String
    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 9, weight: .semibold))
            .foregroundStyle(.white.opacity(0.45))
            .padding(.top, 2)
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

/// A clip chosen for merging — a transcript line or a blooper span. Both index
/// into the same source recording, so both can be composited into the merge.
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
        case .sentence(let c):
            if let s = c.start, let e = c.end, e > s { return e - s }
            return Double(estimatedSeconds(c.text))
        case .blooper(let b): return b.duration
        }
    }

    /// This clip's [start, end] range in the source video — nil for pasted lines
    /// that carry no timecodes (nothing to cut).
    var sourceRange: CMTimeRange? {
        switch self {
        case .sentence(let c):
            guard let s = c.start, let e = c.end, e > s else { return nil }
            return CMTimeRange(start: CMTime(seconds: s, preferredTimescale: 600),
                               end: CMTime(seconds: e, preferredTimescale: 600))
        case .blooper(let b):
            return CMTimeRange(start: CMTime(seconds: b.start, preferredTimescale: 600),
                               end: CMTime(seconds: b.end, preferredTimescale: 600))
        }
    }

    /// The span to send to the backend `/merge` — nil for pasted lines with no
    /// timecodes (nothing to cut).
    var clipSpan: ClipSpan? {
        switch self {
        case .sentence(let c):
            guard let s = c.start, let e = c.end, e > s else { return nil }
            return ClipSpan(start: s, end: e, text: c.text)
        case .blooper(let b):
            return ClipSpan(start: b.start, end: b.end, text: "")
        }
    }
}

/// One clip's background-music window on the merged composition timeline.
private struct BedSegment {
    let start: Double
    let end: Double
    let b64: String
    let volume: Float
}

/// "Next page" after picking clips to merge: concatenates the marked clips
/// (transcript lines + blooper spans) into one composition, plays it, and can
/// export the result to an .mp4 file.
private struct MergePreviewView: View {
    let items: [MergeClip]
    let videoURL: URL?                            // source recording the spans are cut from
    let segments: [TranscriptSegment]            // word times → karaoke captions
    let orientation: ShortOrientation            // landscape (as-is) or portrait (blur fill)
    let captionsOn: Bool                         // burn + preview karaoke captions
    let beds: [Int: BacksoundResponse]           // per-sentence-clip music bed
    let bedVolumes: [Int: Double]                // per-clip bed volume (0–1)
    let onBack: () -> Void

    @State private var player = AVPlayer()
    @State private var hasVideo = false
    @State private var captionWords: [TranscriptWord] = []   // remapped to composition time
    /// Per-clip bed windows on the composition timeline, played by a synced
    /// AVAudioPlayer (avoids AVComposition's deprecated synchronous track APIs).
    @State private var bedSchedule: [BedSegment] = []
    @State private var bedPlayer: AVAudioPlayer?
    @State private var activeBedIndex: Int?
    @State private var bedObserver: Any?
    @State private var buildError: String?
    @State private var isExporting = false
    @State private var exportError: String?
    @State private var exportNotice: String?
    private let service = HighlightService()

    private var totalSeconds: Int { Int(items.reduce(0.0) { $0 + $1.seconds }.rounded()) }
    /// Selected lines with no timecodes — they can't be composited into the video.
    private var textOnlyCount: Int {
        items.filter { if case .sentence(let c) = $0 { return c.start == nil || c.end == nil }; return false }.count
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
        .task { await build() }
        .onDisappear {
            player.pause()
            stopBedSync()
        }
        .alert("Couldn’t export the video",
               isPresented: Binding(get: { exportError != nil },
                                    set: { if !$0 { exportError = nil } })) {
            Button("OK", role: .cancel) { exportError = nil }
        } message: {
            Text(exportError ?? "")
        }
        .alert("Short exported",
               isPresented: Binding(get: { exportNotice != nil },
                                    set: { if !$0 { exportNotice = nil } })) {
            Button("OK", role: .cancel) { exportNotice = nil }
        } message: {
            Text(exportNotice ?? "")
        }
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
            if isExporting { ProgressView().controlSize(.small) }
            Button { exportMerged() } label: { Label("Export Short", systemImage: "square.and.arrow.up") }
                .disabled(!hasVideo || isExporting || videoURL == nil)
                .help("Render the merged clips as a captioned Short. " + orientation.exportHelp)
        }
        .padding()
    }

    @ViewBuilder
    private var preview: some View {
        if hasVideo {
            OrientationVideoCanvas(player: player, portrait: orientation == .portrait)
                .clipShape(RoundedRectangle(cornerRadius: 14))
                // Match the export shape: portrait 9:16 (live blurred fill) vs
                // landscape 16:9, so the orientation toggle is visible.
                .aspectRatio(orientation == .portrait ? 9.0 / 16.0 : 16.0 / 9.0, contentMode: .fit)
                .frame(maxWidth: .infinity, maxHeight: orientation == .portrait ? 480 : 340)
                .animation(.easeInOut(duration: 0.2), value: orientation)
                .overlay {
                    if captionsOn && !captionWords.isEmpty {
                        CaptionOverlay(player: player, words: captionWords,
                                       vertical: orientation == .portrait)
                    }
                }
                .overlay(alignment: .bottomLeading) {
                    VStack(alignment: .leading, spacing: 4) {
                        if orientation == .portrait {
                            Text("Portrait 9:16 · blurred fill")
                                .padding(.horizontal, 8).padding(.vertical, 4)
                                .background(.black.opacity(0.55), in: Capsule())
                        }
                        if textOnlyCount > 0 {
                            Text("\(textOnlyCount) line\(textOnlyCount == 1 ? "" : "s") without timecodes not shown")
                                .padding(.horizontal, 8).padding(.vertical, 4)
                                .background(.black.opacity(0.55), in: Capsule())
                        }
                    }
                    .font(.caption2).foregroundStyle(.white)
                    .padding(10)
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

    /// Concatenate the marked clips (transcript lines + blooper spans, video+audio)
    /// into one composition, in the order they were arranged. Source tracks are
    /// loaded async and inserted per composition track (the non-deprecated path).
    private func build() async {
        guard let videoURL else { return }
        let asset = AVURLAsset(url: videoURL)
        let comp = AVMutableComposition()
        let allWords = segments.flatMap { $0.words ?? [] }
        var cursor = CMTime.zero
        var caps: [TranscriptWord] = []
        var schedule: [BedSegment] = []
        do {
            let srcVideo = try await asset.loadTracks(withMediaType: .video).first
            let srcAudio = try await asset.loadTracks(withMediaType: .audio).first
            let compVideo = comp.addMutableTrack(withMediaType: .video,
                                                 preferredTrackID: kCMPersistentTrackID_Invalid)
            let compAudio = comp.addMutableTrack(withMediaType: .audio,
                                                 preferredTrackID: kCMPersistentTrackID_Invalid)
            for item in items {
                guard let range = item.sourceRange else { continue }
                let offset = cursor.seconds
                // Remap this sentence's words from the source timeline onto the
                // composition timeline (each item is appended at `cursor`).
                if case .sentence(let c) = item, let s = c.start, let e = c.end {
                    for w in allWords where w.start < e && w.end > s {
                        caps.append(TranscriptWord(word: w.word,
                                                   start: offset + (w.start - s),
                                                   end: offset + (w.end - s)))
                    }
                    // Record this clip's bed window on the composition timeline;
                    // a synced AVAudioPlayer plays it during that window.
                    if let bed = beds[c.id] {
                        schedule.append(BedSegment(start: offset, end: offset + range.duration.seconds,
                                                   b64: bed.audioB64,
                                                   volume: Float(bedVolumes[c.id] ?? 0.35)))
                    }
                }
                if let srcVideo { try compVideo?.insertTimeRange(range, of: srcVideo, at: cursor) }
                if let srcAudio { try compAudio?.insertTimeRange(range, of: srcAudio, at: cursor) }
                cursor = cursor + range.duration
            }
            if cursor > .zero {
                captionWords = caps
                bedSchedule = schedule
                player.replaceCurrentItem(with: AVPlayerItem(asset: comp))
                hasVideo = true
                await player.seek(to: .zero)
                player.play()
                startBedSync()
            }
        } catch {
            buildError = "Couldn't build the merge: \(error.localizedDescription)"
        }
    }

    /// Drive the bed audio off the merge player's clock: whenever the playhead
    /// enters a clip that has a bed, loop that bed under it at its chosen volume.
    private func startBedSync() {
        guard bedObserver == nil, !bedSchedule.isEmpty else { return }
        bedObserver = player.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 0.2, preferredTimescale: 600), queue: .main
        ) { time in updateBed(at: time.seconds) }
    }

    private func updateBed(at now: Double) {
        guard let idx = bedSchedule.firstIndex(where: { now >= $0.start && now < $0.end }) else {
            bedPlayer?.stop(); bedPlayer = nil; activeBedIndex = nil
            return
        }
        if activeBedIndex != idx {
            bedPlayer?.stop()
            let seg = bedSchedule[idx]
            if let data = Data(base64Encoded: seg.b64), let p = try? AVAudioPlayer(data: data) {
                p.numberOfLoops = -1
                p.volume = seg.volume
                p.currentTime = (now - seg.start).truncatingRemainder(dividingBy: max(p.duration, 0.01))
                p.play()
                bedPlayer = p
            }
            activeBedIndex = idx
        } else if player.timeControlStatus != .playing {
            bedPlayer?.pause()
        } else if bedPlayer?.isPlaying == false {
            bedPlayer?.play()
        }
    }

    private func stopBedSync() {
        if let bedObserver { player.removeTimeObserver(bedObserver); self.bedObserver = nil }
        bedPlayer?.stop(); bedPlayer = nil; activeBedIndex = nil
    }

    /// Render the merged clips into one portrait (1080×1920) captioned Short via
    /// the backend, then reveal the written file in Finder.
    private func exportMerged() {
        guard let videoURL else { return }
        // Carry each sentence clip's generated bed into its span so the merged
        // Short is mixed with the same music (at the chosen volume) as the preview.
        let spans: [ClipSpan] = items.compactMap { item in
            guard let span = item.clipSpan else { return nil }
            if case .sentence(let c) = item, let bed = beds[c.id] {
                return ClipSpan(start: span.start, end: span.end, text: span.text,
                                musicB64: bed.audioB64,
                                musicVolume: bedVolumes[c.id] ?? 0.35)
            }
            return span
        }
        guard !spans.isEmpty else {
            exportError = "None of the selected clips have timecodes to cut."
            return
        }
        isExporting = true
        exportError = nil
        exportNotice = nil
        Task {
            defer { isExporting = false }
            do {
                let result = try await service.mergeClips(
                    videoPath: videoURL.path, clips: spans, segments: segments,
                    vertical: orientation.vertical, subtitles: captionsOn)
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: result.path)])
                if result.subtitlesRequested && !result.subtitlesApplied {
                    exportNotice = "Exported the \(orientation.rawValue.lowercased()) Short, but karaoke "
                        + "captions were skipped — this ffmpeg has no subtitles support "
                        + "(install an ffmpeg built with libass)."
                }
            } catch {
                exportError = error.localizedDescription
            }
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
        case .sentence(let c):
            if let s = c.start, let e = c.end, e > s {
                return String(format: "%.1fs · %@", e - s, c.text)
            }
            return c.text
        case .blooper(let b): return String(format: "%.2fs · %@", b.duration, b.label)
        }
    }
}

/// An `AVPlayerLayer`-backed video view. Because the layer is just a render
/// surface, several instances can share one `AVPlayer` — which lets us stack a
/// blurred fill behind a sharp copy without a second player.
private struct PlayerLayerView: NSViewRepresentable {
    let player: AVPlayer
    var gravity: AVLayerVideoGravity = .resizeAspect

    func makeNSView(context: Context) -> PlayerLayerNSView {
        let view = PlayerLayerNSView()
        view.playerLayer.player = player
        view.playerLayer.videoGravity = gravity
        return view
    }

    func updateNSView(_ view: PlayerLayerNSView, context: Context) {
        if view.playerLayer.player !== player { view.playerLayer.player = player }
        view.playerLayer.videoGravity = gravity
    }
}

/// Layer-backed NSView that keeps its `AVPlayerLayer` sized to its bounds.
private final class PlayerLayerNSView: NSView {
    let playerLayer = AVPlayerLayer()

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer = CALayer()
        layer?.addSublayer(playerLayer)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) unavailable") }

    override func layout() {
        super.layout()
        playerLayer.frame = bounds
    }
}

/// Preview canvas that mirrors the export framing. Portrait stacks a blurred,
/// aspect-fill copy of the same player behind a sharp aspect-fit copy — the same
/// look ffmpeg burns in on export. Landscape is a single aspect-fit video.
private struct OrientationVideoCanvas: View {
    let player: AVPlayer
    let portrait: Bool

    var body: some View {
        ZStack {
            Color.black
            if portrait {
                PlayerLayerView(player: player, gravity: .resizeAspectFill)
                    .blur(radius: 24)
                    .clipped()
            }
            PlayerLayerView(player: player, gravity: .resizeAspect)
        }
    }
}

/// Live word-by-word karaoke caption overlay, driven by the player's clock. The
/// `words` carry start/end on the *same* timeline the player reports — the source
/// timeline for a single clip, or the composition timeline for the merged preview.
/// This mirrors the burned-in ASS captions (active word boxed) so the preview
/// matches the export.
private struct CaptionOverlay: View {
    let player: AVPlayer
    let words: [TranscriptWord]
    let vertical: Bool

    @State private var now: Double = 0
    @State private var observer: Any?

    private var wordsPerLine: Int { vertical ? 3 : 5 }
    private var groups: [[TranscriptWord]] {
        stride(from: 0, to: words.count, by: wordsPerLine).map {
            Array(words[$0 ..< min($0 + wordsPerLine, words.count)])
        }
    }
    /// The line whose time span currently contains the playhead.
    private var activeGroup: [TranscriptWord]? {
        groups.first { g in
            guard let s = g.first?.start, let e = g.last?.end else { return false }
            return now >= s && now <= e
        }
    }

    /// Font size that keeps the active line within the (often narrow) preview
    /// width — approximates heavy-caps glyph advance so long words shrink to fit.
    private func fontSize(for group: [TranscriptWord], width: CGFloat) -> CGFloat {
        let chars = group.reduce(0) { $0 + max($1.word.count, 1) }
        let denom = CGFloat(chars + group.count * 2) * 0.62   // glyphs + per-word padding
        let fit = (width - 16) / max(denom, 1)
        return min(vertical ? 18 : 16, max(8, fit))
    }

    var body: some View {
        GeometryReader { geo in
            VStack {
                Spacer()
                if let group = activeGroup {
                    let size = fontSize(for: group, width: geo.size.width)
                    HStack(spacing: 4) {
                        ForEach(Array(group.enumerated()), id: \.offset) { _, w in
                            let active = now >= w.start && now < w.end
                            Text(w.word.uppercased())
                                .font(.system(size: size, weight: .heavy))
                                .foregroundStyle(.white)
                                .lineLimit(1)
                                .padding(.horizontal, 4).padding(.vertical, 2)
                                .background(active ? Color.red : Color.black.opacity(0.5),
                                            in: RoundedRectangle(cornerRadius: 4))
                        }
                    }
                    .shadow(radius: 3)
                    .padding(.horizontal, 8)
                    .padding(.bottom, geo.size.height * (vertical ? 0.11 : 0.06))
                }
            }
            .frame(width: geo.size.width, height: geo.size.height)
        }
        .allowsHitTesting(false)
        .onAppear {
            observer = player.addPeriodicTimeObserver(
                forInterval: CMTime(seconds: 0.05, preferredTimescale: 600), queue: .main
            ) { t in now = t.seconds }
        }
        .onDisappear {
            if let observer { player.removeTimeObserver(observer); self.observer = nil }
        }
    }
}

#Preview {
    ContentView()
}
