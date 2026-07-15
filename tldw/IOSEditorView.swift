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
    /// Called when a merged Short finishes exporting, so the Library can record it.
    var onExported: ((URL) -> Void)? = nil
    @Environment(\.dismiss) private var dismiss

    @State private var tool: Tool = .format
    @State private var isPlaying = false

    // Trim drag anchors (start/end in seconds, captured at gesture begin).
    @State private var trimDragStart: Double?
    @State private var trimDragEnd: Double?

    // Timeline reorder drag state.
    @State private var draggingClipID: EditClip.ID?
    @State private var dragOffset: CGFloat = 0

    // Export overlay state
    @State private var exporting = false
    @State private var exportProgress = 0.0
    @State private var exportStatus = ""
    @State private var exportedURL: URL?
    @State private var exportError: String?

    private enum Tool: String, CaseIterable, Identifiable {
        case format = "Format", captions = "Captions", music = "Music", trim = "Trim"
        var id: String { rawValue }
        var icon: String {
            switch self {
            case .format:   return "aspectratio"
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
        PlayerLayerView(player: model.player)
            .aspectRatio(model.orientation.aspect, contentMode: .fit)
            .frame(maxWidth: .infinity, maxHeight: .infinity)   // fill the width edge to edge
            .animation(.easeInOut, value: model.orientation)
            .contentShape(Rectangle())
            .onTapGesture {
                if isPlaying { model.pause() } else { model.playSelected() }
                isPlaying.toggle()
            }
    }

    // MARK: timeline

    // Thumb width (46) + HStack spacing (8) — one clip's slot on the timeline.
    private let timelineItemW: CGFloat = 54

    private var timeline: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(Array(model.clips.enumerated()), id: \.element.id) { i, clip in
                    thumb(clip, index: i)
                        .offset(x: draggingClipID == clip.id ? dragOffset : 0)
                        .scaleEffect(draggingClipID == clip.id ? 1.08 : 1)
                        .shadow(color: .black.opacity(draggingClipID == clip.id ? 0.4 : 0),
                                radius: 6, y: 3)
                        .zIndex(draggingClipID == clip.id ? 1 : 0)
                        .onTapGesture { model.select(clip); isPlaying = false; model.pause() }
                        .gesture(reorderGesture(clip))
                }
            }
            .padding(.horizontal, 16)
            .animation(.easeInOut(duration: 0.18), value: model.clips.map(\.id))
        }
        .frame(height: 76)
        .padding(.vertical, 8)
    }

    /// Press-and-hold a clip, then drag it left/right to reorder — the hold lets
    /// the gesture win over the horizontal scroll. Neighbours shift live.
    private func reorderGesture(_ clip: EditClip) -> some Gesture {
        LongPressGesture(minimumDuration: 0.22)
            .sequenced(before: DragGesture(minimumDistance: 0))
            .onChanged { value in
                guard case .second(true, let drag?) = value else { return }
                if draggingClipID == nil {
                    draggingClipID = clip.id
                    model.select(clip); isPlaying = false; model.pause()
                }
                dragOffset = drag.translation.width
                guard let from = model.clips.firstIndex(where: { $0.id == clip.id }) else { return }
                let target = from + Int((dragOffset / timelineItemW).rounded())
                if target != from, model.clips.indices.contains(target) {
                    model.move(from: from, to: target)
                    dragOffset -= CGFloat(target - from) * timelineItemW
                }
            }
            .onEnded { _ in
                withAnimation(.easeInOut(duration: 0.18)) { dragOffset = 0 }
                draggingClipID = nil
            }
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
        // Delete affordance on the selected clip (Order tab is gone).
        .overlay(alignment: .topTrailing) {
            if isSel && model.clips.count > 1 {
                Button { model.delete(clip) } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 15))
                        .foregroundStyle(.white, .black.opacity(0.55))
                }
                .buttonStyle(.plain)
                .offset(x: 6, y: -6)
            }
        }
    }

    // MARK: tool tabs

    /// Tools available. Music (AI backsound) and Captions (burned subtitles) are
    /// disabled for now — hide those tabs.
    private var availableTools: [Tool] {
        Tool.allCases.filter { $0 != .music && $0 != .captions }
    }

    private var toolTabs: some View {
        HStack(spacing: 0) {
            ForEach(availableTools) { t in
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
            case .format:   formatPanel
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

    private var formatPanel: some View {
        HStack(spacing: 16) {
            ForEach(ClipOrientation.allCases) { o in
                let isSel = model.orientation == o
                Button { model.setOrientation(o) } label: {
                    VStack(spacing: 8) {
                        RoundedRectangle(cornerRadius: 5)
                            .strokeBorder(isSel ? AnyShapeStyle(g) : AnyShapeStyle(Color.white.opacity(0.35)),
                                          lineWidth: 2)
                            .aspectRatio(o.aspect, contentMode: .fit)
                            .frame(height: 48)
                            .overlay(Image(systemName: o.icon).font(.caption)
                                .foregroundStyle(isSel ? .white : .white.opacity(0.5)))
                        Text("\(o.rawValue) · \(o.ratioLabel)")
                            .font(.caption)
                            .foregroundStyle(isSel ? .white : .white.opacity(0.6))
                    }
                }
                .buttonStyle(.plain)
            }
            Spacer()
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

    @ViewBuilder
    private var trimPanel: some View {
        if let clip = model.selected {
            VStack(spacing: 10) {
                filmstripTrimmer(for: clip)
                    .frame(height: 64)
                HStack {
                    Text(timecode(clip.start))
                    Spacer()
                    Text(String(format: "%.1fs", clip.duration)).foregroundStyle(.white.opacity(0.85))
                    Spacer()
                    Text(timecode(clip.end))
                }
                .font(.caption2.monospaced())
                .foregroundStyle(.white.opacity(0.6))
            }
        } else {
            Text("No clip selected.").font(.footnote).foregroundStyle(.secondary)
        }
    }

    /// A macOS-style filmstrip clip: a repeated-thumbnail band whose lit region is
    /// the kept span. Drag the left/right handles to trim start/end; the window
    /// gives a little room on each side so the clip can also be extended.
    private func filmstripTrimmer(for clip: EditClip) -> some View {
        // Window shown in the strip: the clip plus padding, clamped to the video.
        let pad = max(1.0, clip.duration * 0.5)
        let lo = max(0, clip.start - pad)
        let hi = min(model.videoDuration, clip.end + pad)
        let span = max(0.1, hi - lo)

        return GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height
            // Reserve a margin on both sides so the handles are always fully
            // visible (never clipped at the panel edge, even for a clip at the
            // very start/end of the video).
            let inset: CGFloat = 24
            let innerW = max(1, w - inset * 2)
            let pps = innerW / span                              // pixels per second
            let startX = inset + CGFloat(clip.start - lo) * pps
            let endX = inset + CGFloat(clip.end - lo) * pps

            ZStack(alignment: .topLeading) {
                filmstripBand(width: w)                          // thumbnails fill the strip

                // Dim the trimmed-away edges.
                Color.black.opacity(0.55).frame(width: startX, height: h)
                Color.black.opacity(0.55).frame(width: max(0, w - endX), height: h)
                    .offset(x: endX)

                // Kept-span frame.
                RoundedRectangle(cornerRadius: 8)
                    .stroke(g, lineWidth: 3)
                    .frame(width: max(1, endX - startX), height: h)
                    .offset(x: startX)

                trimHandle(center: startX, height: h) { dx in
                    if trimDragStart == nil { trimDragStart = clip.start }
                    let s = min(max(lo, (trimDragStart ?? clip.start) + Double(dx / pps)), clip.end - 0.2)
                    model.setTrim(start: s, end: clip.end)
                } onEnd: { trimDragStart = nil }

                trimHandle(center: endX, height: h) { dx in
                    if trimDragEnd == nil { trimDragEnd = clip.end }
                    let e = max(min(hi, (trimDragEnd ?? clip.end) + Double(dx / pps)), clip.start + 0.2)
                    model.setTrim(start: clip.start, end: e)
                } onEnd: { trimDragEnd = nil }
            }
            .frame(width: w, height: h)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }

    private func filmstripBand(width: CGFloat) -> some View {
        let tile: CGFloat = 46
        let count = max(1, Int((width / tile).rounded(.up)))
        return HStack(spacing: 0) {
            ForEach(0..<count, id: \.self) { _ in
                Group {
                    if let img = model.thumbnails[model.selectedID ?? UUID()] {
                        Image(uiImage: img).resizable().scaledToFill()
                    } else {
                        Rectangle().fill(.white.opacity(0.08))
                    }
                }
                .frame(width: tile, height: 64)
                .clipped()
            }
        }
    }

    /// A grab handle centered horizontally at `center`, with a wide invisible hit
    /// area so it's easy to drag with a fingertip.
    private func trimHandle(center: CGFloat, height: CGFloat,
                            onDrag: @escaping (CGFloat) -> Void,
                            onEnd: @escaping () -> Void) -> some View {
        let hit: CGFloat = 40
        return RoundedRectangle(cornerRadius: 6)
            .fill(g)
            .frame(width: 16, height: height)
            .overlay(RoundedRectangle(cornerRadius: 1.5).fill(.white)
                .frame(width: 2.5, height: 20))
            .frame(width: hit, height: height)        // wide touch target
            .contentShape(Rectangle())
            .offset(x: center - hit / 2)
            .gesture(
                DragGesture(minimumDistance: 1)
                    .onChanged { onDrag($0.translation.width) }
                    .onEnded { _ in onEnd() }
            )
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
            onExported?(url)
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
