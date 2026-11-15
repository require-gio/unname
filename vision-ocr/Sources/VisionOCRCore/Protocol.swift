// Protocol 1 between the sidecar and the helper.

import Foundation

public let protocolVersion = 1
public let helperVersion = "1.0.0"
/// The only revision stage 1 uses. If it is missing, the helper exits with
/// code 5 instead of silently switching to another one.
public let textRevision = 3

/// Exit codes; the sidecar maps them to ``OcrUnavailableError``.
public enum ExitCode: Int32, Sendable {
    case success = 0
    case usage = 2
    case inputTooLarge = 3
    case recognitionFailed = 4
    case unsupported = 5
}

/// Error with an exit code. The message names only the kind of error, never content.
public struct HelperError: Error, CustomStringConvertible, Sendable {
    public let code: ExitCode
    public let message: String

    public init(_ code: ExitCode, _ message: String) {
        self.code = code
        self.message = message
    }

    public var description: String { message }
}

public struct InfoResponse: Codable, Equatable, Sendable {
    public var engine = "apple-vision"
    public var `protocol` = protocolVersion
    public var helper = helperVersion
    public var os: String
    public var osBuild: String
    public var textRevisions: [Int]
    public var documentRevisions: [Int]
    public var languages: [String]

    enum CodingKeys: String, CodingKey {
        case engine, `protocol`, helper, os, languages
        case osBuild = "os_build"
        case textRevisions = "text_revisions"
        case documentRevisions = "document_revisions"
    }
}

public enum TileSpec: Equatable, Sendable {
    case auto
    case grid(columns: Int, rows: Int)

    public init?(argument: String) {
        if argument == "auto" {
            self = .auto
            return
        }
        let parts = argument.split(separator: "x")
        guard parts.count == 2, let columns = Int(parts[0]), let rows = Int(parts[1]),
              (1...3).contains(columns), (1...3).contains(rows)
        else { return nil }
        self = .grid(columns: columns, rows: rows)
    }

    public var label: String {
        switch self {
        case .auto: "auto"
        case let .grid(columns, rows): "\(columns)x\(rows)"
        }
    }
}

/// Where the pixels of a tile come from; both ways were compared in a spike.
public enum TileSource: String, Sendable {
    /// ``regionOfInterest`` on the same image.
    case region
    /// A separate crop per tile.
    case crop
}

public enum RecognitionMode: String, Sendable {
    case text
    case document
}

public struct RecognizeOptions: Equatable, Sendable {
    public var languages: [String]
    public var tiles: TileSpec = .auto
    public var tileSource: TileSource = .crop
    public var mode: RecognitionMode = .text
    public var languageCorrection = false
    public var qos: QualityOfService = .userInitiated

    public init(languages: [String]) {
        self.languages = languages
    }
}

public struct WordOutput: Codable, Equatable, Sendable {
    public var text: String
    public var left: Int
    public var top: Int
    public var width: Int
    public var height: Int
    /// ``[block, paragraph, line]``, each starting at 1.
    public var line: [Int]
    public var confidence: Double
}

public struct RecognizeResponse: Codable, Equatable, Sendable {
    public var `protocol` = protocolVersion
    public var request: String
    public var revision: Int
    public var osBuild: String
    public var width: Int
    public var height: Int
    public var tiles: String
    public var elapsedMs: Int
    public var words: [WordOutput]

    enum CodingKeys: String, CodingKey {
        case `protocol`, request, revision, width, height, tiles, words
        case osBuild = "os_build"
        case elapsedMs = "elapsed_ms"
    }
}

public func encodeLine<T: Encodable>(_ value: T) throws -> Data {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    var data = try encoder.encode(value)
    data.append(0x0A)
    return data
}
