//
//  BlooperView.swift
//  tldw — Blooper (non-speech) detector
//
//  An inline panel docked under the editor's timeline. Pick a source video
//  (drag-and-drop or browse); detection runs in the Python backend (Silero VAD +
//  an optional OpenCV lip check) and the detected non-speech spans are reported
//  up to the editor, which drops them onto the timeline. Previewing a span
//  happens in the editor's big preview, so this panel has no player of its own.
//
//  For now the video is chosen explicitly here; eventually it should default to
//  the source of the selected clip on the timeline.
//

import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct BlooperPanel: View {
    /// Reports the detected spans + their source video up to the editor.
    var onBloopers: ([BlooperSpan], URL?) -> Void = { _, _ in }

    @State private var videoURL: URL?
    @State private var response: BlooperResponse?
    @State private var isLoading = false
    @State private var statusMessage = ""
    @State private var errorMessage: String?
    @State private var useLipCheck = true
    @State private var isDropTargeted = false

    private let service = HighlightService()

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            content
        }
        .padding(.horizontal, 20).padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor))
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
            } else if videoURL != nil {
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

    // MARK: - Content (drop zone / status)

    @ViewBuilder
    private var content: some View {
        if isLoading {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text(statusMessage).font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
        } else if videoURL == nil {
            dropZone
        } else if let response {
            Label(response.bloopers.isEmpty
                  ? "No non-speech spans — someone's talking the whole way through."
                  : "Added \(response.count) clip\(response.count == 1 ? "" : "s") to the timeline — click a red one to preview it above.",
                  systemImage: response.bloopers.isEmpty ? "checkmark.circle" : "arrow.up")
                .font(.caption).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, minHeight: 32, alignment: .leading)
        }
    }

    /// Drag-and-drop target for a source video (also click-to-browse).
    private var dropZone: some View {
        Button { pickVideo() } label: {
            ZStack {
                RoundedRectangle(cornerRadius: 12)
                    .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
                    .foregroundStyle(isDropTargeted ? Color.accentColor : Color.secondary.opacity(0.45))
                VStack(spacing: 6) {
                    Image(systemName: "arrow.down.doc")
                        .font(.system(size: 28))
                        .foregroundStyle(isDropTargeted ? Color.accentColor : .secondary)
                    Text(isDropTargeted ? "Drop to scan" : "Drag a video here")
                        .font(.callout.weight(.medium))
                    Text("or click to browse — spans land on the timeline, no clips exported")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity).frame(height: 120)
            .background(isDropTargeted ? Color.accentColor.opacity(0.08) : Color.clear,
                        in: RoundedRectangle(cornerRadius: 12))
            .contentShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
        .dropDestination(for: URL.self) { urls, _ in handleDrop(urls) }
            isTargeted: { isDropTargeted = $0 }
    }

    // MARK: - Video picking + detection

    private func pickVideo() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.movie, .video, .mpeg4Movie, .quickTimeMovie]
        panel.message = "Choose a source video to scan for bloopers."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        load(url)
    }

    /// Accept a dropped file, ignoring anything that isn't a video.
    private func handleDrop(_ urls: [URL]) -> Bool {
        guard let url = urls.first(where: isVideo) else { return false }
        load(url)
        return true
    }

    private func isVideo(_ url: URL) -> Bool {
        if let type = UTType(filenameExtension: url.pathExtension) {
            return type.conforms(to: .audiovisualContent) || type.conforms(to: .movie)
        }
        return ["mov", "mp4", "m4v", "avi", "mkv", "webm"].contains(url.pathExtension.lowercased())
    }

    private func load(_ url: URL) {
        // Dropped files come with a sandbox extension; hold it open so the editor's
        // AVPlayer can keep reading (no-op for NSOpenPanel URLs, already granted).
        _ = url.startAccessingSecurityScopedResource()
        videoURL = url
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
                let result = try await service.detectBloopers(
                    videoPath: url.path, useLipCheck: useLipCheck)
                response = result
                errorMessage = nil
                onBloopers(result.bloopers, url)   // push spans + source into the editor timeline
            } catch {
                response = nil
                errorMessage = error.localizedDescription
                onBloopers([], nil)
            }
        }
    }
}
