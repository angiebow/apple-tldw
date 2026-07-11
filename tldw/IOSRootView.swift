//
//  IOSRootView.swift
//  tldw (iOS)
//
//  The iOS app flow: pick a video → compress → transcribe + rank on the cloud
//  backend → choose Shorts → export/merge → share. macOS UI is AppKit-only and
//  compiled out on iOS; this is the SwiftUI counterpart on the same tldwKit
//  HighlightService.
//

#if os(iOS)
import SwiftUI
import PhotosUI
import UniformTypeIdentifiers
import tldwKit

// MARK: - Brand style

private enum Brand {
    static let g1 = Color(red: 0.45, green: 0.26, blue: 0.96)   // indigo
    static let g2 = Color(red: 0.93, green: 0.28, blue: 0.55)   // pink
    static let gradient = LinearGradient(colors: [g1, g2],
                                         startPoint: .topLeading, endPoint: .bottomTrailing)
    static let accent = g1
    static func card(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(white: 0.12) : Color(white: 0.97)
    }
}

// MARK: - Root

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
                    StartView(pickerItem: $pickerItem, message: model.statusMessage,
                              isError: model.serverReachable == false)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(.systemBackground))
            .navigationTitle(model.hasResults ? "Your Shorts" : "")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if model.hasResults {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("New", systemImage: "plus") { reset() }
                    }
                }
            }
        }
        .tint(Brand.accent)
        .onChange(of: pickerItem) { _, item in
            guard let item else { return }
            Task { await loadAndProcess(item) }
        }
        .sheet(isPresented: $showingExport) {
            ExportSheet(exporter: exporter)
        }
    }

    @MainActor
    private func loadAndProcess(_ item: PhotosPickerItem) async {
        selected = []
        model.isLoading = true
        model.loadingStage = 0
        model.statusMessage = "Preparing your video…"
        do {
            guard let movie = try await item.loadTransferable(type: Movie.self) else {
                model.isLoading = false
                model.statusMessage = "Couldn't load that video. Try another."
                model.serverReachable = nil
                return
            }
            // Shrink to 720p before upload (fast, and fits tunnel/proxy limits).
            let prepared = (try? await VideoCompressor.compress(movie.url)) ?? movie.url
            await model.transcribeAndHighlight(at: prepared)
        } catch {
            model.isLoading = false
            model.statusMessage = error.localizedDescription
        }
    }

    private func startExport(merge: Bool) {
        let all = model.viralLines + model.relevantLines
        var seen = Set<Int>()
        let chosen = all.filter { selected.contains($0.index) && seen.insert($0.index).inserted }
        guard let source = model.sourceVideoURL, !chosen.isEmpty else { return }
        exporter.reset()
        showingExport = true
        Task {
            if merge {
                await exporter.exportMerged(source: source, lines: chosen, segments: model.transcriptSegments)
            } else {
                await exporter.exportClips(source: source, lines: chosen, segments: model.transcriptSegments)
            }
        }
    }

    private func reset() {
        selected = []
        pickerItem = nil
        model.reset()
    }
}

// MARK: - Start

private struct StartView: View {
    @Binding var pickerItem: PhotosPickerItem?
    let message: String
    let isError: Bool

    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            ZStack {
                Circle().fill(Brand.gradient)
                    .frame(width: 108, height: 108)
                    .shadow(color: Brand.g1.opacity(0.4), radius: 20, y: 10)
                Image(systemName: "scissors")
                    .font(.system(size: 44, weight: .semibold))
                    .foregroundStyle(.white)
            }
            Text("ViReel")
                .font(.system(size: 40, weight: .heavy, design: .rounded))
                .foregroundStyle(Brand.gradient)
                .padding(.top, 22)
            Text("Turn any video into viral Shorts")
                .font(.title3.weight(.medium))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.top, 6)

            if isError {
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(.red.opacity(0.12), in: Capsule())
                    .padding(.horizontal, 24)
                    .padding(.top, 20)
            }

            Spacer()

            PhotosPicker(selection: $pickerItem, matching: .videos) {
                Label("Choose a video", systemImage: "wand.and.stars")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .background(Brand.gradient, in: RoundedRectangle(cornerRadius: 16))
                    .foregroundStyle(.white)
                    .shadow(color: Brand.g1.opacity(0.35), radius: 14, y: 8)
            }
            .padding(.horizontal, 24)

            Text("Powered by on-device AI · private by design")
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .padding(.top, 14)
                .padding(.bottom, 24)
        }
    }
}

// MARK: - Loading

private struct LoadingView: View {
    let model: HighlightViewModel

    private var stages: [(String, String)] {
        [("waveform", "Transcribing"), ("sparkles", "Finding highlights"), ("checkmark", "Ready")]
    }

    var body: some View {
        VStack(spacing: 28) {
            Spacer()
            ZStack {
                Circle().stroke(Brand.g1.opacity(0.15), lineWidth: 6)
                Circle().trim(from: 0, to: 0.28)
                    .stroke(Brand.gradient, style: StrokeStyle(lineWidth: 6, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .modifier(Spin())
                Image(systemName: stages[min(model.loadingStage, 2)].0)
                    .font(.system(size: 30, weight: .semibold))
                    .foregroundStyle(Brand.gradient)
            }
            .frame(width: 96, height: 96)

            VStack(spacing: 8) {
                Text(stages[min(model.loadingStage, 2)].1)
                    .font(.title3.bold())
                Text(model.statusMessage)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 36)
            }

            // Stage pips
            HStack(spacing: 10) {
                ForEach(0..<3) { i in
                    Capsule()
                        .fill(i <= model.loadingStage ? AnyShapeStyle(Brand.gradient) : AnyShapeStyle(Color.secondary.opacity(0.2)))
                        .frame(width: i == model.loadingStage ? 28 : 8, height: 8)
                        .animation(.spring, value: model.loadingStage)
                }
            }
            Spacer()
        }
    }
}

private struct Spin: ViewModifier {
    @State private var on = false
    func body(content: Content) -> some View {
        content
            .rotationEffect(.degrees(on ? 360 : 0))
            .animation(.linear(duration: 1).repeatForever(autoreverses: false), value: on)
            .onAppear { on = true }
    }
}

// MARK: - Results

private struct ResultsView: View {
    let model: HighlightViewModel
    @Binding var selected: Set<Int>
    let onMerge: () -> Void
    let onClips: () -> Void
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 10) {
                Text("Tap the Shorts you want, then export or merge.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)

                sectionHeader("Most viral", "flame.fill", .orange)
                ForEach(model.viralLines) { card($0, viral: true) }
                if !model.relevantLines.isEmpty {
                    sectionHeader("Most relevant", "scope", Brand.accent)
                    ForEach(model.relevantLines) { card($0, viral: false) }
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 4)
            .padding(.bottom, 12)
        }
        // Keep dense content readable — don't let large accessibility text blow
        // the cards up.
        .dynamicTypeSize(...DynamicTypeSize.large)
        .safeAreaInset(edge: .bottom) { exportBar }
    }

    private func sectionHeader(_ title: String, _ icon: String, _ color: Color) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon).font(.caption).foregroundStyle(color)
            Text(title).font(.subheadline.weight(.semibold))
            Spacer()
        }
        .padding(.top, 4)
    }

    private func card(_ line: LineScore, viral: Bool) -> some View {
        let isOn = selected.contains(line.index)
        return Button {
            if isOn { selected.remove(line.index) } else { selected.insert(line.index) }
        } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: isOn ? "checkmark.circle.fill" : "circle")
                    .font(.body)
                    .foregroundStyle(isOn ? AnyShapeStyle(Brand.gradient) : AnyShapeStyle(Color.secondary.opacity(0.5)))
                VStack(alignment: .leading, spacing: 6) {
                    Text(line.text)
                        .font(.footnote)
                        .foregroundStyle(.primary)
                        .lineLimit(3)
                        .multilineTextAlignment(.leading)
                    HStack(spacing: 8) {
                        if let s = line.start, let e = line.end {
                            Label(timecode(s) + "–" + timecode(e), systemImage: "timer")
                                .font(.caption2.monospaced())
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        scoreBar(viral ? line.viralScore : line.relevance, viral: viral)
                    }
                }
            }
            .padding(12)
            .background(Brand.card(scheme), in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14)
                .strokeBorder(isOn ? AnyShapeStyle(Brand.gradient) : AnyShapeStyle(Color.clear), lineWidth: 1.5))
        }
        .buttonStyle(.plain)
    }

    private func scoreBar(_ score: Double, viral: Bool) -> some View {
        let pct = max(0, min(1, score))
        return HStack(spacing: 5) {
            Image(systemName: viral ? "flame.fill" : "target")
                .font(.caption2).foregroundStyle(viral ? .orange : Brand.accent)
            ZStack(alignment: .leading) {
                Capsule().fill(Color.secondary.opacity(0.2)).frame(width: 40, height: 4)
                Capsule().fill(viral ? AnyShapeStyle(Color.orange) : AnyShapeStyle(Brand.gradient))
                    .frame(width: 40 * pct, height: 4)
            }
        }
    }

    private var exportBar: some View {
        HStack(spacing: 10) {
            Button(action: onClips) {
                Label("Export \(selected.count)", systemImage: "square.and.arrow.up")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity).padding(.vertical, 12)
                    .background(Brand.card(scheme), in: RoundedRectangle(cornerRadius: 12))
            }
            Button(action: onMerge) {
                Label("Merge", systemImage: "film.stack")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity).padding(.vertical, 12)
                    .background(Brand.gradient, in: RoundedRectangle(cornerRadius: 12))
                    .foregroundStyle(.white)
            }
        }
        .foregroundStyle(.primary)
        .disabled(selected.isEmpty)
        .opacity(selected.isEmpty ? 0.5 : 1)
        .padding(.horizontal, 16).padding(.vertical, 10)
        .background(.bar)
    }

    private func timecode(_ seconds: Double) -> String {
        let t = Int(seconds.rounded()); return String(format: "%d:%02d", t / 60, t % 60)
    }
}

// MARK: - Export sheet

private struct ExportSheet: View {
    let exporter: IOSExporter
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: 22) {
                Spacer()
                switch exporter.phase {
                case .working, .idle:
                    ZStack {
                        Circle().stroke(Brand.g1.opacity(0.15), lineWidth: 8).frame(width: 120, height: 120)
                        Circle().trim(from: 0, to: max(0.02, exporter.progress))
                            .stroke(Brand.gradient, style: StrokeStyle(lineWidth: 8, lineCap: .round))
                            .rotationEffect(.degrees(-90)).frame(width: 120, height: 120)
                            .animation(.easeInOut, value: exporter.progress)
                        Text("\(Int(exporter.progress * 100))%").font(.title3.bold().monospacedDigit())
                    }
                    Text(exporter.statusText).font(.callout).foregroundStyle(.secondary)
                case .done:
                    Image(systemName: "checkmark.circle.fill").font(.system(size: 60)).foregroundStyle(Brand.gradient)
                    Text(shareTitle).font(.title3.bold())
                    ShareLink(items: exporter.shareItems) {
                        Label("Share", systemImage: "square.and.arrow.up")
                            .font(.headline).frame(maxWidth: .infinity).padding(.vertical, 16)
                            .background(Brand.gradient, in: RoundedRectangle(cornerRadius: 16))
                            .foregroundStyle(.white)
                    }
                    .padding(.horizontal, 24)
                case .failed:
                    Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 48)).foregroundStyle(.orange)
                    Text(exporter.errorMessage ?? "Export failed.")
                        .font(.callout).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center).padding(.horizontal, 32)
                }
                Spacer()
            }
            .padding()
            .navigationTitle("Export").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
    }

    private var shareTitle: String {
        exporter.mergedURL != nil ? "Your Short is ready" : "\(exporter.clipURLs.count) clip(s) ready"
    }
}

// MARK: - PhotosPicker → local file

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
