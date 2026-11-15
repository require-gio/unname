// swift-tools-version: 6.0
//
// unname-vision-ocr: text recognition with Apple Vision for the sidecar.
// Image on stdin, JSON on stdout; no files, no paths, no network.

import PackageDescription

let package = Package(
    name: "unname-vision-ocr",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "unname-vision-ocr", targets: ["unname-vision-ocr"]),
    ],
    targets: [
        // The core is a library so the tests can call it without a process;
        // the program itself is only invocation and exit codes.
        .target(
            name: "VisionOCRCore",
            linkerSettings: [
                .linkedFramework("Vision"),
                .linkedFramework("CoreGraphics"),
                .linkedFramework("ImageIO"),
            ]
        ),
        .executableTarget(
            name: "unname-vision-ocr",
            dependencies: ["VisionOCRCore"]
        ),
        .testTarget(
            name: "VisionOCRCoreTests",
            dependencies: ["VisionOCRCore"],
            linkerSettings: [.linkedFramework("CoreText")]
        ),
    ]
)
