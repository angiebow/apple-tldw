//
//  IOSAppShell.swift
//  tldw (iOS)
//
//  The app's top-level structure: a bottom tab bar (Create / Library / Settings)
//  so opening the app lands on a real home screen instead of a bare upload prompt.
//  The Create tab hosts the pick → transcribe → results → editor flow; Library
//  lists the Shorts made this session; Settings shows the backend connection.
//

#if os(iOS)
import SwiftUI
import tldwKit

// MARK: - Library store (session)

/// Records Shorts/clips exported during this session so the Library tab has real
/// content. Session-only for now — a saved-to-disk library can come later.
@MainActor
@Observable
final class LibraryStore {
    struct Item: Identifiable {
        let id = UUID()
        let url: URL
        let title: String
        let subtitle: String
        let date: Date
    }
    private(set) var items: [Item] = []

    func add(_ url: URL, title: String, subtitle: String) {
        items.insert(Item(url: url, title: title, subtitle: subtitle, date: .now), at: 0)
    }
}

// MARK: - Tab shell

struct IOSAppShell: View {
    @State private var library = LibraryStore()
    @State private var tab = 0

    var body: some View {
        TabView(selection: $tab) {
            CreateTab(library: library, switchToLibrary: { tab = 1 })
                .tabItem { Label("Create", systemImage: "wand.and.stars") }
                .tag(0)

            LibraryTab(library: library, createNew: { tab = 0 })
                .tabItem { Label("Library", systemImage: "square.stack.3d.up") }
                .tag(1)

            SettingsTab()
                .tabItem { Label("Settings", systemImage: "gearshape") }
                .tag(2)
        }
        .tint(Brand.accent)
    }
}

// MARK: - Library tab

private struct LibraryTab: View {
    let library: LibraryStore
    let createNew: () -> Void

    var body: some View {
        NavigationStack {
            Group {
                if library.items.isEmpty {
                    ContentUnavailableView {
                        Label("No Shorts yet", systemImage: "square.stack.3d.up.slash")
                    } description: {
                        Text("Shorts you export appear here for the current session.")
                    } actions: {
                        Button(action: createNew) {
                            Label("Create a Short", systemImage: "wand.and.stars")
                                .font(.subheadline.weight(.semibold))
                                .padding(.horizontal, 18).padding(.vertical, 10)
                                .background(Brand.gradient, in: Capsule())
                                .foregroundStyle(.white)
                        }
                    }
                } else {
                    List(library.items) { item in
                        ShareLink(item: item.url) {
                            HStack(spacing: 12) {
                                ZStack {
                                    RoundedRectangle(cornerRadius: 10).fill(Brand.gradient)
                                        .frame(width: 46, height: 60)
                                    Image(systemName: "play.fill").foregroundStyle(.white)
                                }
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(item.title).font(.subheadline.weight(.semibold))
                                        .foregroundStyle(.primary).lineLimit(1)
                                    Text(item.subtitle).font(.caption).foregroundStyle(.secondary)
                                    Text(item.date, format: .dateTime.hour().minute())
                                        .font(.caption2).foregroundStyle(.tertiary)
                                }
                                Spacer()
                                Image(systemName: "square.and.arrow.up").foregroundStyle(Brand.accent)
                            }
                        }
                        .buttonStyle(.plain)
                    }
                    .listStyle(.plain)
                }
            }
            .navigationTitle("Library")
        }
    }
}

// MARK: - Settings tab

private struct SettingsTab: View {
    @State private var reachable: Bool?
    @State private var checking = false
    private let service = HighlightService()

    private var host: String { BackendConfig.baseURL.absoluteString }

    var body: some View {
        NavigationStack {
            List {
                Section("Backend") {
                    LabeledContent("Server") {
                        Text(host).font(.footnote.monospaced())
                            .foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                    }
                    HStack {
                        Text("Status")
                        Spacer()
                        if checking {
                            ProgressView().controlSize(.small)
                        } else {
                            Label(reachable == true ? "Connected"
                                  : reachable == false ? "Unreachable" : "Unknown",
                                  systemImage: reachable == true ? "checkmark.circle.fill"
                                  : reachable == false ? "xmark.circle.fill" : "questionmark.circle")
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(reachable == true ? .green
                                                 : reachable == false ? .red : .secondary)
                        }
                    }
                    Button("Test connection") { Task { await check() } }
                        .disabled(checking)
                }

                Section("About") {
                    LabeledContent("App", value: "ViReel")
                    LabeledContent("Version", value: appVersion)
                    Text("Turn any video into viral Shorts. Your recording is processed on your own backend — private by design.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .task { await check() }
        }
    }

    private func check() async {
        checking = true
        reachable = await service.health()
        checking = false
    }

    private var appVersion: String {
        let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0"
        let b = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "1"
        return "\(v) (\(b))"
    }
}
#endif
