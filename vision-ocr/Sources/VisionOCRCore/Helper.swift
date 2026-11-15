// The two commands ``info`` and ``recognize`` without process details, so
// the tests can call them directly.

import Darwin
import Foundation

public func systemVersion() -> (version: String, build: String) {
    let version = ProcessInfo.processInfo.operatingSystemVersion
    var rendered = "\(version.majorVersion).\(version.minorVersion)"
    if version.patchVersion > 0 { rendered += ".\(version.patchVersion)" }
    var size = 0
    sysctlbyname("kern.osversion", nil, &size, nil, 0)
    var buffer = [UInt8](repeating: 0, count: max(size, 1))
    let build = sysctlbyname("kern.osversion", &buffer, &size, nil, 0) == 0
        ? String(decoding: buffer.prefix { $0 != 0 }, as: UTF8.self) : ""
    return (rendered, build)
}

public func info() throws -> InfoResponse {
    let system = systemVersion()
    let capabilities: (revisions: [Int], languages: [String])
    do {
        capabilities = try textCapabilities()
    } catch {
        throw HelperError(.unsupported, "Die Texterkennung ist auf diesem System nicht verfuegbar.")
    }
    return InfoResponse(
        os: system.version,
        osBuild: system.build,
        textRevisions: capabilities.revisions,
        documentRevisions: documentRevisions(),
        languages: capabilities.languages
    )
}

public func recognize(_ data: Data, options: RecognizeOptions) throws -> RecognizeResponse {
    let started = DispatchTime.now()
    let image = try loadImage(data)
    let words: [OrderedWord]
    let tiles: String
    let request: String
    let revision: Int
    switch options.mode {
    case .text:
        (words, tiles) = try recognizeText(image, options: options)
        request = "text"
        revision = textRevision
    case .document:
        words = try recognizeDocument(image, options: options)
        tiles = "1x1"
        request = "document"
        revision = 1
    }
    let elapsed = Int((DispatchTime.now().uptimeNanoseconds - started.uptimeNanoseconds) / 1_000_000)
    return RecognizeResponse(
        request: request,
        revision: revision,
        osBuild: systemVersion().build,
        width: image.width,
        height: image.height,
        tiles: tiles,
        elapsedMs: elapsed,
        words: words.map { item in
            WordOutput(
                text: item.word.text,
                left: item.word.rect.left,
                top: item.word.rect.top,
                width: item.word.rect.width,
                height: item.word.rect.height,
                line: [item.block, item.paragraph, item.line],
                confidence: (item.word.confidence * 1000).rounded() / 1000
            )
        }
    )
}

/// Arguments of ``recognize``; unknown options are a usage error.
public func parseRecognizeArguments(_ arguments: [String]) throws -> RecognizeOptions {
    var options = RecognizeOptions(languages: [])
    var index = 0
    func value() throws -> String {
        index += 1
        guard index < arguments.count else { throw HelperError(.usage, "Wert fehlt fuer \(arguments[index - 1]).") }
        return arguments[index]
    }
    while index < arguments.count {
        switch arguments[index] {
        case "--languages":
            options.languages = try value().split(separator: ",").map(String.init)
        case "--tiles":
            guard let spec = TileSpec(argument: try value()) else { throw HelperError(.usage, "--tiles ist ungueltig.") }
            options.tiles = spec
        case "--tile-source":
            guard let source = TileSource(rawValue: try value()) else { throw HelperError(.usage, "--tile-source ist ungueltig.") }
            options.tileSource = source
        case "--mode":
            guard let mode = RecognitionMode(rawValue: try value()) else { throw HelperError(.usage, "--mode ist ungueltig.") }
            options.mode = mode
        case "--correction":
            switch try value() {
            case "on": options.languageCorrection = true
            case "off": options.languageCorrection = false
            default: throw HelperError(.usage, "--correction ist ungueltig.")
            }
        case "--qos":
            guard let qos = QualityOfService(argument: try value()) else { throw HelperError(.usage, "--qos ist ungueltig.") }
            options.qos = qos
        default:
            throw HelperError(.usage, "Unbekannte Option.")
        }
        index += 1
    }
    guard !options.languages.isEmpty else { throw HelperError(.usage, "--languages fehlt.") }
    return options
}

extension QualityOfService {
    init?(argument: String) {
        switch argument {
        case "user-interactive": self = .userInteractive
        case "user-initiated": self = .userInitiated
        case "utility": self = .utility
        case "background": self = .background
        default: return nil
        }
    }

    public var qosClass: qos_class_t {
        switch self {
        case .userInteractive: QOS_CLASS_USER_INTERACTIVE
        case .userInitiated: QOS_CLASS_USER_INITIATED
        case .utility: QOS_CLASS_UTILITY
        case .background: QOS_CLASS_BACKGROUND
        default: QOS_CLASS_DEFAULT
        }
    }
}
