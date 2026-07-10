//
//  BackendConfig.swift
//  tldwKit
//
//  Resolves the backend base URL so the same client works against a local dev
//  server, a staging host, or production — selected per build/scheme rather than
//  hard-coded in the client.
//

import Foundation

public enum BackendConfig {
    /// Optional `BackendConfig.plist` bundled in the app — the simplest way to
    /// point the app at a backend (edit one file; no Xcode build-setting dance).
    /// Falls back to the app's Info.plist, then to build-type defaults.
    private static let overrides: [String: Any] = {
        guard let url = Bundle.main.url(forResource: "BackendConfig", withExtension: "plist"),
              let dict = NSDictionary(contentsOf: url) as? [String: Any] else {
            return [:]
        }
        return dict
    }()

    /// Resolve a config string from BackendConfig.plist, else Info.plist. nil when
    /// absent or blank.
    private static func value(for key: String) -> String? {
        for source in [overrides[key], Bundle.main.object(forInfoDictionaryKey: key)] {
            if let raw = source as? String {
                let trimmed = raw.trimmingCharacters(in: .whitespaces)
                if !trimmed.isEmpty { return trimmed }
            }
        }
        return nil
    }

    /// Base URL of the tldw backend. Resolution order:
    /// 1. `BackendBaseURL` in a bundled `BackendConfig.plist`.
    /// 2. `BackendBaseURL` in the app's Info.plist.
    /// 3. localhost in DEBUG builds, the production host in Release.
    public static var baseURL: URL {
        if let raw = value(for: "BackendBaseURL"), let url = URL(string: raw) {
            return url
        }
        #if DEBUG
        return URL(string: "http://127.0.0.1:8000")!
        #else
        // TODO: point this at the real production backend once it is deployed.
        return URL(string: "https://api.tldw.example")!
        #endif
    }

    /// Bearer token sent as `Authorization: Bearer <token>` to the cloud backend
    /// (`BackendAPIToken` in BackendConfig.plist or Info.plist). nil when unset —
    /// dev backends without a token leave auth off.
    public static var apiToken: String? {
        value(for: "BackendAPIToken")
    }
}
