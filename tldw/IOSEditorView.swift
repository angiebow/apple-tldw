//
//  IOSEditorView.swift
//  tldw (iOS)
//
//  The merged-timeline editor, modeled on docs/design/ios-editor-reference.png:
//  a large 9:16 preview, a horizontal clip timeline, tool tabs, a per-tool panel,
//  and a bottom action bar. Tools map to backend features (Order / Captions /
//  Music / Trim) — no transitions. ✓ exports the merged Short.
//

#if os(iOS)
import SwiftUI
import AVFoundation
import tldwKit

struct IOSEditorView: View {
    @State var model: IOSEditorModel
    @Environment(\.dismiss) private var dismiss

    @State private var tool: Tool = .order
    @State private var isPlaying = false

    // Export overlay state
    @State private var exporting = false
    @State private var exportProgress = 0.0
    @State private var exportStatus = ""
    @State private var exportedURL: URL?
    @State private var exportError: String?

    private enum Tool: String, CaseIterable, Identifiable {
        case order = "Order", captions = "Captions", music = "Music", trim = "Trim"
        var id: String { rawValue }
        var icon: String {
            switch self {
            case .order:    return "arrow.left.arrow.right"
            case .captions: return "captions.bubble"
            case .music:    return "music.note"
            case .trim:     return "timeline.selection"
            }
        }
    }

    private let g = LinearGradient(colors: [Color(red: 0.45, green: 0.26, blue: 0.96),
                                            Color(red: 0.93, green: 0.28, blue: 0.55)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing)

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 0) {
                topBar
                preview
                timeline
                toolTabs
                toolPanel
                    .frame(minHeight: 128, alignment: .top)
                bottomBar
            }
            if exporting || exportedURL != nil || exportError != nil { exportOverlay }
        }
        .task { await model.load() }
        .preferredColorScheme(.dark)
    }

    // MARK: top bar

    private var topBar: some View {
        ZStack {
            Text("Editor").font(.headline)
            HStack {
                Spacer()
                Button { model.pause(); dismiss() } label: {
                    Image(systemName: "xmark")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(.white)
                        .padding(8)
                        .background(.white.opacity(0.15), in: Circle())
                }
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
    }

    // MARK: preview

    private var preview: some View {
        ZStack {
            PlayerLayerView(player: model.player)
                .aspectRatio(9.0 / 16.0, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 16))
            Button {
                if isPlaying { model.pause() } else { model.playSelected() }
                isPlaying.toggle()
            } label: {
                Image(systemName: isPlaying ? "pause.circle.fill" : "play.circle.fill")
                    .font(.system(size: 54))
                    .foregroundStyle(.white.opacity(0.9))
                    .shadow(radius: 8)
            }
        }
        .padding(.horizontal, 16)
        .frame(maxHeight: .infinity)
    }

    // MARK: timeline

    private var timeline: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(Array(model.clips.enumerated()), id: \.element.id) { i, clip in
                    Button { model.select(clip); isPlaying = false; model.pause() } label: {
                        thumb(clip, index: i)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 16)
        }
        .frame(height: 76)
        .padding(.vertical, 8)
    }

    private func thumb(_ clip: EditClip, index: Int) -> some View {
        let isSel = clip.id == model.selectedID
        return ZStack(alignment: .bottomLeading) {
            Group {
                if let img = model.thumbnails[clip.id] {
                    Image(uiImage: img).resizable().scaledToFill()
                } else {
                    Rectangle().fill(.white.opacity(0.08))
                }
            }
            .frame(width: 46, height: 60)
            .clipShape(RoundedRectangle(cornerRadius: 6))
            Text("\(index + 1)")
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(.white)
                .padding(3)
        }
        .overlay(RoundedRectangle(cornerRadius: 6)
            .strokeBorder(isSel ? AnyShapeStyle(g) : AnyShapeStyle(Color.white.opacity(0.15)),
                          lineWidth: isSel ? 2 : 1))
    }

    // MARK: tool tabs

    private var toolTabs: some View {
        HStack(spacing: 0) {
            ForEach(Tool.allCases) { t in
                Button { tool = t } label: {
                    VStack(spacing: 4) {
                        Image(systemName: t.icon).font(.body)
                        Text(t.rawValue).font(.caption2)
                    }
                    .frame(maxWidth: .infinity)
                    .foregroundStyle(tool == t ? .white : .white.opacity(0.5))
                }
            }
        }
        .padding(.vertical, 10)
        .background(.white.opacity(0.04))
    }

    // MARK: per-tool panel

    @ViewBuilder
    private var toolPanel: some View {
        VStack(spacing: 14) {
            switch tool {
            case .order:    orderPanel
            case .captions: togglePanel(
                title: "Burn captions", subtitle: "Karaoke subtitles on the Short",
                isOn: Binding(get: { model.captions }, set: { model.captions = $0 }))
            case .music:    musicPanel
            case .trim:     trimPanel
            }
        }
        .padding(16)
        .animation(.default, value: tool)
    }

    private var orderPanel: some View {
        VStack(spacing: 10) {
            if let clip = model.selected {
                Text(clip.text).font(.footnote).foregroundStyle(.white.opacity(0.85))
                    .lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
                HStack(spacing: 12) {
                    roundButton("arrow.left", "Move left") { model.moveLeft(clip) }
                    roundButton("arrow.right", "Move right") { model.moveRight(clip) }
                    Spacer()
                    roundButton("trash", "Remove", tint: .red) { model.delete(clip) }
                }
            } else {
                Text("No clips left — go back and pick some Shorts.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
    }

    private func togglePanel(title: String, subtitle: String, isOn: Binding<Bool>) -> some View {
        Toggle(isOn: isOn) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.medium)).foregroundStyle(.white)
                Text(subtitle).font(.caption).foregroundStyle(.white.opacity(0.6))
            }
        }
        .tint(Color(red: 0.55, green: 0.30, blue: 0.96))
    }

    private var musicPanel: some View {
        VStack(spacing: 12) {
            togglePanel(title: "Background music",
                        subtitle: "AI music bed matched to the mood",
                        isOn: Binding(get: { model.backsound }, set: { model.backsound = $0 }))
            if model.backsound {
                HStack {
                    Image(systemName: "speaker.fill").foregroundStyle(.white.opacity(0.6))
                    Slider(value: Binding(get: { model.backsoundVolume },
                                          set: { model.backsoundVolume = $0 }), in: 0.05...1)
                    Image(systemName: "speaker.wave.3.fill").foregroundStyle(.white.opacity(0.6))
                }
            }
        }
    }

    private var trimPanel: some View {
        VStack(spacing: 10) {
            if let clip = model.selected {
                trimRow("Start", value: clip.start, range: 0...max(0.1, clip.end - 0.1)) { s in
                    model.setTrim(start: s, end: clip.end)
                }
                trimRow("End", value: clip.end, range: min(clip.start + 0.1, model.videoDuration)...model.videoDuration) { e in
                    model.setTrim(start: clip.start, end: e)
                }
                Text(String(format: "Length %.1fs", clip.duration))
                    .font(.caption).foregroundStyle(.white.opacity(0.6))
            }
        }
    }

    private func trimRow(_ label: String, value: Double, range: ClosedRange<Double>,
                         onChange: @escaping (Double) -> Void) -> some View {
        HStack(spacing: 10) {
            Text(label).font(.caption).foregroundStyle(.white.opacity(0.7)).frame(width: 40, alignment: .leading)
            Slider(value: Binding(get: { value }, set: onChange), in: range)
                .tint(Color(red: 0.55, green: 0.30, blue: 0.96))
            Text(timecode(value)).font(.caption2.monospaced()).foregroundStyle(.white.opacity(0.7))
                .frame(width: 48, alignment: .trailing)
        }
    }

    private func roundButton(_ icon: String, _ label: String, tint: Color = .white,
                             action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(label, systemImage: icon)
                .font(.caption.weight(.medium))
                .foregroundStyle(tint)
                .padding(.horizontal, 12).padding(.vertical, 8)
                .background(.white.opacity(0.08), in: Capsule())
        }
    }

    // MARK: bottom action bar

    private var bottomBar: some View {
        HStack {
            Text("\(model.clips.count) clip\(model.clips.count == 1 ? "" : "s") · \(Int(model.totalDuration))s")
                .font(.caption).foregroundStyle(.white.opacity(0.6))
            Spacer()
            Button { Task { await runExport() } } label: {
                Label("Export", systemImage: "checkmark")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 20).padding(.vertical, 12)
                    .background(g, in: Capsule())
            }
            .disabled(model.clips.isEmpty)
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .background(.white.opacity(0.04))
    }

    // MARK: export overlay

    private var exportOverlay: some View {
        ZStack {
            Color.black.opacity(0.75).ignoresSafeArea()
            VStack(spacing: 20) {
                if let url = exportedURL {
                    Image(systemName: "checkmark.circle.fill").font(.system(size: 56)).foregroundStyle(g)
                    Text("Your Short is ready").font(.headline).foregroundStyle(.white)
                    ShareLink(item: url) {
                        Label("Share", systemImage: "square.and.arrow.up")
                            .font(.headline).foregroundStyle(.white)
                            .padding(.horizontal, 22).padding(.vertical, 12)
                            .background(g, in: Capsule())
                    }
                    Button("Done") { dismiss() }.foregroundStyle(.white.opacity(0.7))
                } else if let err = exportError {
                    Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 44)).foregroundStyle(.orange)
                    Text(err).font(.callout).foregroundStyle(.white.opacity(0.8))
                        .multilineTextAlignment(.center).padding(.horizontal, 30)
                    Button("Back") { exportError = nil }.foregroundStyle(.white.opacity(0.7))
                } else {
                    ProgressView(value: exportProgress).progressViewStyle(.linear).tint(.white).frame(width: 200)
                    Text(exportStatus.isEmpty ? "Exporting…" : exportStatus)
                        .font(.callout).foregroundStyle(.white.opacity(0.8))
                }
            }
            .padding(28)
        }
    }

    private func runExport() async {
        model.pause(); isPlaying = false
        exporting = true; exportProgress = 0; exportError = nil; exportedURL = nil
        do {
            let url = try await model.export { fraction, stage in
                Task { @MainActor in
                    exportProgress = fraction
                    if let stage { exportStatus = stage }
                }
            }
            exportedURL = url
        } catch {
            exportError = error.localizedDescription
        }
        exporting = false
    }

    private func timecode(_ seconds: Double) -> String {
        let t = Int(seconds.rounded()); return String(format: "%d:%02d", t / 60, t % 60)
    }
}

// MARK: - AVPlayer preview layer

struct PlayerLayerView: UIViewRepresentable {
    let player: AVPlayer
    func makeUIView(context: Context) -> PlayerUIView {
        let view = PlayerUIView()
        view.playerLayer.player = player
        view.playerLayer.videoGravity = .resizeAspect
        return view
    }
    func updateUIView(_ view: PlayerUIView, context: Context) {
        view.playerLayer.player = player
    }
}

final class PlayerUIView: UIView {
    override class var layerClass: AnyClass { AVPlayerLayer.self }
    var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
}
#endif
