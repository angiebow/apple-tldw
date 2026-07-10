//
//  IOSRootView.swift
//  tldw
//
//  Temporary iOS entry screen. The macOS UI (ContentView/BlooperView) is
//  AppKit-only and compiled out on iOS via `#if os(macOS)`. The real iOS
//  upload → poll → download flow lands in Phase 2; for now this screen just
//  confirms the iOS build compiles, links `tldwKit`, and can resolve the
//  backend base URL from `BackendConfig`.
//

#if os(iOS)
import SwiftUI
import tldwKit

struct IOSRootView: View {
    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "scissors")
                .font(.system(size: 48))
            Text("ViReel")
                .font(.largeTitle.bold())
            Text("iOS client coming in Phase 2")
                .foregroundStyle(.secondary)
            Text(BackendConfig.baseURL.absoluteString)
                .font(.footnote.monospaced())
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding()
    }
}

#Preview {
    IOSRootView()
}
#endif
