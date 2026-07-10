//
//  IOSRootView.swift
//  tldw (iOS)
//
//  The iOS app flow: pick a video → transcribe + rank on the cloud backend →
//  choose Shorts → export/merge → share. The macOS UI (ContentView/BlooperView)
//  is AppKit-only and compiled out on iOS; this is the SwiftUI iOS counterpart,
//  built on the same tldwKit HighlightService.
//

#if os(iOS)
import SwiftUI
import PhotosUI
import UniformTypeIdentifiers
import tldwKit

struct IOSRootView: View {
    @State private var model = HighlightViewModel()
    @State private var exporter = IOSExporter()
    @State private var pickerItem: PhotosPickerItem?
    @State private var selected: Set<Int> = []
    @State private var showingExport = false

    var body: some View {
        NavigationStack {
            Group {
                if model.isLoading {
                    LoadingView(model: model)
                } else if model.hasResults {
                    ResultsView(model: model, selected: $selected,
                                onMerge: { startExport(merge: true) },
                                onClips: { startExport(merge: false) })
                } else {
                    StartView(pickerItem: $pickerItem, message: model.statusMessage)
                }
            }
            .navigationTitle("ViReel")
            .toolbar {
                if model.hasResults {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("New") { reset() }
                    }
                }
            }
        }
        .onChange(of: pickerItem) { _, item in
            guard let item else { return }
            Task { await loadAndProcess(item) }
        }
        .sheet(isPresented: $showingExport) {
            ExportSheet(exporter: exporter)
        }
    }

    private func loadAndProcess(_ item: PhotosPickerItem) async {
        selected = []
        do {
            guard let movie = try await item.loadTransferable(type: Movie.self) else {
                model.statusMessage = "Couldn't load that video. Try another."
                return
            }
            await model.transcribeAndHighlight(at: movie.url)
        } catch {
            model.statusMessage = error.localizedDescription
        }
    }

    private func startExport(merge: Bool) {
        // Dedupe selected lines by index across the viral + relevant lists.
        let all = model.viralLines + model.relevantLines
        var seen = Set<Int>()
        let chosen = all.filter { selected.contains($0.index) && seen.insert($0.index).inserted }
        guard let source = model.sourceVideoURL, !chosen.isEmpty else { return }

        exporter.reset()
        showingExport = true
        Task {
            if merge {
                await exporter.exportMerged(source: source, lines: chosen,
                                            segments: model.transcriptSegments)
            } else {
                await exporter.exportClips(source: source, lines: chosen,
                                           segments: model.transcriptSegments)
            }
        }
    }

    private func reset() {
        selected = []
        pickerItem = nil
        model.reset()
    }
}

// MARK: - Start (empty state)

private struct StartView: View {
    @Binding var pickerItem: PhotosPickerItem?
    let message: String

    var body: some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "scissors")
                .font(.system(size: 52))
                .foregroundStyle(.tint)
            Text("Turn a video into Shorts")
                .font(.title2.bold())
            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            PhotosPicker(selection: $pickerItem, matching: .videos) {
                Label("Choose a video", systemImage: "photo.on.rectangle")
                    .font(.headline)
                    .padding(.horizontal, 20).padding(.vertical, 12)
                    .background(.tint, in: Capsule())
                    .foregroundStyle(.white)
            }
            Spacer()
            Text(BackendConfig.baseURL.absoluteString)
                .font(.footnote.monospaced())
                .foregroundStyle(.tertiary)
        }
        .padding()
    }
}

// MARK: - Loading

private struct LoadingView: View {
    let model: HighlightViewModel

    var body: some View {
        VStack(spacing: 18) {
            Spacer()
            ProgressView().controlSize(.large)
            Text(stageTitle)
                .font(.headline)
            Text(model.statusMessage)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            Spacer()
        }
        .padding()
    }

    private var stageTitle: String {
        switch model.loadingStage {
        case 0: return "Transcribing…"
        case 1: return "Finding highlights…"
        default: return "Almost there…"
        }
    }
}

// MARK: - Results (ranked Shorts + selection)

private struct ResultsView: View {
    let model: HighlightViewModel
    @Binding var selected: Set<Int>
    let onMerge: () -> Void
    let onClips: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            List {
                if !model.viralLines.isEmpty {
                    Section("Most viral") {
                        ForEach(model.viralLines) { line in row(line) }
                    }
                }
                if !model.relevantLines.isEmpty {
                    Section("Most relevant") {
                        ForEach(model.relevantLines) { line in row(line) }
                    }
                }
            }
            .listStyle(.insetGrouped)

            exportBar
        }
    }

    private func row(_ line: LineScore) -> some View {
        let isOn = selected.contains(line.index)
        return Button {
            if isOn { selected.remove(line.index) } else { selected.insert(line.index) }
        } label: {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: isOn ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isOn ? Color.accentColor : .secondary)
                    .font(.title3)
                VStack(alignment: .leading, spacing: 4) {
                    Text(line.text).font(.callout).foregroundStyle(.primary)
                    HStack(spacing: 10) {
                        if line.viralLabel {
                            Label("viral", systemImage: "flame.fill")
                                .font(.caption2).foregroundStyle(.orange)
                        }
                        if let s = line.start, let e = line.end {
                            Text(timecode(s) + " – " + timecode(e))
                                .font(.caption2.monospaced()).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var exportBar: some View {
        HStack(spacing: 12) {
            Button(action: onClips) {
                Label("Export clips", systemImage: "square.and.arrow.up.on.square")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            Button(action: onMerge) {
                Label("Merge", systemImage: "film.stack")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
        }
        .disabled(selected.isEmpty)
        .padding()
        .background(.bar)
    }

    private func timecode(_ seconds: Double) -> String {
        let total = Int(seconds.rounded())
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}

// MARK: - Export sheet (progress → share)

private struct ExportSheet: View {
    let exporter: IOSExporter
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                switch exporter.phase {
                case .working, .idle:
                    Spacer()
                    ProgressView(value: exporter.progress)
                        .progressViewStyle(.linear)
                        .padding(.horizontal, 40)
                    Text(exporter.statusText).font(.callout).foregroundStyle(.secondary)
                    Spacer()
                case .done:
                    Spacer()
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 48)).foregroundStyle(.green)
                    Text(shareTitle).font(.headline)
                    ShareLink(items: exporter.shareItems) {
                        Label("Share", systemImage: "square.and.arrow.up")
                            .font(.headline)
                            .padding(.horizontal, 20).padding(.vertical, 12)
                            .background(.tint, in: Capsule())
                            .foregroundStyle(.white)
                    }
                    Spacer()
                case .failed:
                    Spacer()
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 44)).foregroundStyle(.orange)
                    Text(exporter.errorMessage ?? "Export failed.")
                        .font(.callout).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center).padding(.horizontal, 32)
                    Spacer()
                }
            }
            .padding()
            .navigationTitle("Export")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private var shareTitle: String {
        if exporter.mergedURL != nil { return "Your Short is ready" }
        return "\(exporter.clipURLs.count) clip(s) ready"
    }
}

// MARK: - PhotosPicker → local file

/// Copies a picked video out of the Photos library into a temp file we own, so
/// the backend upload can read it.
struct Movie: Transferable {
    let url: URL

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(contentType: .movie) { movie in
            SentTransferredFile(movie.url)
        } importing: { received in
            let ext = received.file.pathExtension.isEmpty ? "mov" : received.file.pathExtension
            let dest = FileManager.default.temporaryDirectory
                .appendingPathComponent("vireel-source-\(UUID().uuidString).\(ext)")
            try? FileManager.default.removeItem(at: dest)
            try FileManager.default.copyItem(at: received.file, to: dest)
            return Movie(url: dest)
        }
    }
}

#Preview {
    IOSRootView()
}
#endif
