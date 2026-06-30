//
//  BlooperView.swift
//  tldw — Blooper (non-speech) detector
//
//  An inline panel docked under the editor's timeline. Pick a source video, find
//  every span where nobody is talking (silence, pauses, dead air), and preview
//  each one by seeking the source video. Detection runs in the Python backend
//  (Silero VAD + an optional OpenCV lip check); this panel never reads cut clips
//  — it scrubs the original file in place.
//
//  For now the video is chosen explicitly here; eventually it should default to
//  the source of the selected clip on the timeline.
//

import SwiftUI
import AppKit
import AVKit
import UniformTypeIdentifiers

struct BlooperPanel: View {
    @State private var videoURL: URL?
    @State private var response: BlooperResponse?
    @State private var isLoading = false
    @State private var statusMessage = ""
    @State private var errorMessage: String?
    @State private var useLipCheck = true

    @State private var player = AVPlayer()
    @State private var playingID: Int?
    @State private var endObserver: Any?

    private let service = HighlightService()

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            content
        }
        .padding(.horizontal, 20).padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor))
        .onDisappear { teardownPlayer() }
    }

    // MARK: - Header (title + video input)

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "waveform.badge.exclamationmark")
                .font(.title3)
                .foregroundStyle(response == nil ? .secondary : Color.orange)
            VStack(alignment: .leading, spacing: 1) {
                Text("Bloopers").font(.callout.weight(.semibold))
                Text(headerSubtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()

            Toggle("Lip check", isOn: $useLipCheck)
                .toggleStyle(.checkbox)
                .help("Confirm the mouth is still (OpenCV). Slower, but flags spans where someone mouths silently.")
                .disabled(isLoading)

            if isLoading {
                ProgressView().controlSize(.small)
            } else if videoURL == nil {
                Button { pickVideo() } label: { Label("Choose video…", systemImage: "film") }
                    .buttonStyle(.borderedProminent)
            } else {
                Button { runDetection() } label: { Label("Re-scan", systemImage: "arrow.clockwise") }
                    .buttonStyle(.bordered)
                Button { pickVideo() } label: { Label("Change", systemImage: "film") }
                    .buttonStyle(.bordered)
            }
        }
    }

    private var headerSubtitle: String {
        if let errorMessage { return errorMessage }
        if isLoading { return statusMessage }
        if let response { return "\(response.count) non-speech span\(response.count == 1 ? "" : "s") · \(response.source)" }
        return "Scan a source video for non-speech / dead-air spans"
    }

    // MARK: - Content (player + span list)

    @ViewBuilder
    private var content: some View {
        if isLoading {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text(statusMessage).font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, minHeight: 60, alignment: .leading)
        } else if let response, videoURL != nil {
            HStack(alignment: .top, spacing: 14) {
                VideoPlayer(player: player)
                    .frame(width: 300, height: 168)
                    .background(Color.black)
                    .clipShape(RoundedRectangle(cornerRadius: 8))

                if response.bloopers.isEmpty {
                    Text("No non-speech spans — someone's talking the whole way through.")
                        .font(.callout).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, minHeight: 168, alignment: .center)
                } else {
                    ScrollView {
                        LazyVStack(spacing: 6) {
                            ForEach(response.bloopers) { span in
                                BlooperRow(span: span,
                                           isPlaying: playingID == span.id,
                                           onPlay: { play(span: span) })
                            }
                        }
                    }
                    .frame(height: 168)
                }
            }
        } else if videoURL == nil {
            Text("Pick a video to find the dead air in it. Each span plays in place — no clips are exported.")
                .font(.caption).foregroundStyle(.tertiary)
                .frame(maxWidth: .infinity, minHeight: 32, alignment: .leading)
        }
    }

    // MARK: - Video picking + detection

    private func pickVideo() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.movie, .video, .mpeg4Movie, .quickTimeMovie]
        panel.message = "Choose a source video to scan for bloopers."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        videoURL = url
        loadPlayer(url: url)
        runDetection()
    }

    private func runDetection() {
        guard let url = videoURL else { return }
        isLoading = true
        errorMessage = nil
        statusMessage = "Extracting audio and running voice-activity detection… first run downloads Silero VAD."
        Task {
            defer { isLoading = false }
            do {
                response = try await service.detectBloopers(
                    videoPath: url.path, useLipCheck: useLipCheck)
                errorMessage = nil
            } catch {
                response = nil
                errorMessage = error.localizedDescription
            }
        }
    }

    // MARK: - Player

    private func loadPlayer(url: URL) {
        teardownPlayer()
        player.replaceCurrentItem(with: AVPlayerItem(url: url))
        playingID = nil
    }

    /// Seek to the span's start and play, stopping at its end.
    private func play(span: BlooperSpan) {
        guard let item = player.currentItem else { return }
        if let endObserver { player.removeTimeObserver(endObserver); self.endObserver = nil }

        let start = CMTime(seconds: span.start, preferredTimescale: 600)
        let end = CMTime(seconds: span.end, preferredTimescale: 600)
        playingID = span.id

        player.pause()
        item.seek(to: start, toleranceBefore: .zero, toleranceAfter: .zero) { _ in
            player.play()
        }

        // Pause the moment playback crosses the span's end.
        endObserver = player.addBoundaryTimeObserver(
            forTimes: [NSValue(time: end)], queue: .main
        ) { [weak player] in
            player?.pause()
            if playingID == span.id { playingID = nil }
        }
    }

    private func teardownPlayer() {
        player.pause()
        if let endObserver { player.removeTimeObserver(endObserver); self.endObserver = nil }
        playingID = nil
    }
}

// MARK: - One blooper span row

private struct BlooperRow: View {
    let span: BlooperSpan
    let isPlaying: Bool
    let onPlay: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Button(action: onPlay) {
                Image(systemName: isPlaying ? "pause.circle.fill" : "play.circle.fill")
                    .font(.system(size: 26))
                    .foregroundStyle(isPlaying ? Color.orange : Color.accentColor)
            }
            .buttonStyle(.plain)
            .help("Play this span from the source video")

            VStack(alignment: .leading, spacing: 2) {
                Text("\(timecode(span.start)) – \(timecode(span.end))")
                    .font(.callout.weight(.semibold).monospacedDigit())
                Text(String(format: "%.2fs of dead air", span.duration))
                    .font(.caption2).foregroundStyle(.secondary)
            }

            Spacer()
            labelBadge
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(Color(nsColor: .windowBackgroundColor),
                    in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8)
            .stroke(isPlaying ? Color.orange : Color.clear, lineWidth: 1.5))
    }

    private var labelBadge: some View {
        let (text, color, icon): (String, Color, String) = {
            switch span.label {
            case "silent":      return ("silent", .green, "speaker.slash.fill")
            case "lips_moving": return ("lips moving", .orange, "mouth.fill")
            default:            return ("no face", .secondary, "person.fill.questionmark")
            }
        }()
        return Label(text, systemImage: icon)
            .font(.caption2.weight(.medium))
            .foregroundStyle(color)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(color.opacity(0.14), in: Capsule())
    }
}

/// Seconds → `M:SS` (or `M:SS.t` under a minute) timecode.
private func timecode(_ seconds: Double) -> String {
    let m = Int(seconds) / 60
    let s = seconds - Double(m * 60)
    return m > 0 ? String(format: "%d:%05.2f", m, s) : String(format: "0:%05.2f", s)
}
