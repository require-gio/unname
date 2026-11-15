// swift-tools-version: 6.0
//
// unname-download: the app's download service, as an XPC service. It downloads
// model files over HTTPS from the allowed hosts and passes the bytes through a
// pipe to the shell. It sees no documents, no paths and no files of the app;
// it is the only part with network rights.

import PackageDescription

let package = Package(
    name: "unname-download",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "unname-download", targets: ["unname-download"]),
    ],
    targets: [
        // The validation rules are a library so the tests can call them
        // without XPC and without a network.
        .target(name: "DownloadCore"),
        .executableTarget(
            name: "unname-download",
            dependencies: ["DownloadCore"],
            // libxpc objects are not Sendable; the program keeps them only on
            // its own queue.
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .testTarget(
            name: "DownloadCoreTests",
            dependencies: ["DownloadCore"]
        ),
    ]
)
