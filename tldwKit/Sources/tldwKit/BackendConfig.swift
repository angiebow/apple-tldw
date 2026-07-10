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
    /// Base URL of the tldw backend. Resolution order:
    ///
    /// 1. `BackendBaseURL` string key in the app's Info.plist. Inject this
    ///    per-scheme via a user-defined build setting / xcconfig
    ///    (e.g. `BACKEND_BASE_URL = https://staging.example.com`) and add
    ///    `BackendBaseURL = $(BACKEND_BASE_URL)` to Info.plist.
    /// 2. Otherwise: localhost in DEBUG builds, the production host in Release.
    public static var baseURL: URL {
        if let raw = Bundle.main.object(forInfoDictionaryKey: "BackendBaseURL") as? String,
           !raw.trimmingCharacters(in: .whitespaces).isEmpty,
           let url = URL(string: raw) {
            return url
        }
        #if DEBUG
        return URL(string: "http://127.0.0.1:8000")!
        #else
        // TODO: point this at the real production backend once it is deployed.
        return URL(string: "https://api.tldw.example")!
        #endif
    }
}
