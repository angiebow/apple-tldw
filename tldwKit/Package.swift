// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "tldwKit",
    platforms: [
        .macOS(.v14),
        .iOS(.v17),
    ],
    products: [
        // Shared networking + wire types used by both the macOS and iOS apps.
        .library(name: "tldwKit", targets: ["tldwKit"]),
    ],
    targets: [
        .target(name: "tldwKit"),
    ]
)
