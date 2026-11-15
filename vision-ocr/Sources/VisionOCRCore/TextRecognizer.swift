// Stage 1: VNRecognizeTextRequest, revision 3, whole page and tiles.

import CoreGraphics
import Foundation
import Vision

/// Revisions and languages this system supports.
public func textCapabilities() throws -> (revisions: [Int], languages: [String]) {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.revision = VNRecognizeTextRequestRevision3
    let revisions = VNRecognizeTextRequest.supportedRevisions.map { Int($0) }.sorted()
    let languages = try request.supportedRecognitionLanguages()
    return (revisions, languages)
}

public func checkSupport(languages: [String]) throws {
    let capabilities: (revisions: [Int], languages: [String])
    do {
        capabilities = try textCapabilities()
    } catch {
        throw HelperError(.unsupported, "Die Texterkennung ist auf diesem System nicht verfuegbar.")
    }
    guard capabilities.revisions.contains(textRevision) else {
        throw HelperError(.unsupported, "Revision \(textRevision) fehlt auf diesem System.")
    }
    for language in languages where !capabilities.languages.contains(language) {
        throw HelperError(.unsupported, "Sprache nicht unterstuetzt: \(language)")
    }
}

/// Recognizes an image: first the whole page, then the tiles, merged and in
/// reading order. If the text is rotated or upside down, the image is
/// recognized upright and every box is mapped back to the original.
public func recognizeText(_ image: InputImage, options: RecognizeOptions) throws -> (words: [OrderedWord], tiles: String) {
    try checkSupport(languages: options.languages)
    let handler = VNImageRequestHandler(cgImage: image.cgImage, options: [:])
    var counter = 0
    let (page, votes) = try run(
        handler: handler,
        region: CGRect(x: 0, y: 0, width: 1, height: 1),
        frame: PixelRect(left: 0, top: 0, width: image.width, height: image.height),
        image: image,
        options: options,
        source: 0,
        counter: &counter
    )
    let orientation = dominantOrientation(votes)
    guard orientation != .upright else {
        return try recognizeUpright(image, handler: handler, page: page, counter: counter, options: options)
    }
    let upright = try rotateUpright(image, orientation)
    var (words, tiles) = try recognizeUpright(upright, handler: nil, page: nil, counter: 0, options: options)
    for index in words.indices {
        words[index].word.rect = orientation.mapBack(words[index].word.rect, width: image.width, height: image.height)
    }
    return (words, tiles)
}

/// Tiles, merging, reading order and tightening on one upright image;
/// ``page`` is the already-read whole page.
private func recognizeUpright(
    _ image: InputImage,
    handler existing: VNImageRequestHandler?,
    page known: [RawWord]?,
    counter start: Int,
    options: RecognizeOptions
) throws -> (words: [OrderedWord], tiles: String) {
    let (columns, rows) = tileGrid(width: image.width, height: image.height, spec: options.tiles)
    let grid = tiles(width: image.width, height: image.height, columns: columns, rows: rows)
    let full = PixelRect(left: 0, top: 0, width: image.width, height: image.height)
    let handler = existing ?? VNImageRequestHandler(cgImage: image.cgImage, options: [:])
    var observationCounter = start
    let page = try known ?? run(
        handler: handler,
        region: CGRect(x: 0, y: 0, width: 1, height: 1),
        frame: full,
        image: image,
        options: options,
        source: 0,
        counter: &observationCounter
    ).words
    var tileResults: [(tile: Tile, words: [RawWord])] = []
    for (index, tile) in grid.enumerated() {
        let words: [RawWord]
        switch options.tileSource {
        case .region:
            words = try run(
                handler: handler,
                region: normalizedRegion(tile.rect, imageWidth: image.width, imageHeight: image.height),
                frame: tile.rect,
                image: image,
                options: options,
                source: index + 1,
                counter: &observationCounter
            ).words
        case .crop:
            guard let cropped = image.cgImage.cropping(to: CGRect(
                x: tile.rect.left, y: tile.rect.top, width: tile.rect.width, height: tile.rect.height
            )) else { throw HelperError(.recognitionFailed, "Eine Kachel laesst sich nicht ausschneiden.") }
            words = try run(
                handler: VNImageRequestHandler(cgImage: cropped, options: [:]),
                region: CGRect(x: 0, y: 0, width: 1, height: 1),
                frame: tile.rect,
                image: image,
                options: options,
                source: index + 1,
                counter: &observationCounter
            ).words
        }
        tileResults.append((tile, words))
    }
    let merged = mergeWords(page: page, tiles: tileResults)
    var ordered = readingOrder(merged, pageWidth: image.width)
    tighten(&ordered, image: image)
    return (ordered, "\(columns)x\(rows)")
}

/// Rotates an image so that text in ``orientation`` stands upright.
func rotateUpright(_ image: InputImage, _ orientation: TextOrientation) throws -> InputImage {
    let size = orientation.uprightSize(width: image.width, height: image.height)
    guard let context = CGContext(
        data: nil,
        width: size.width,
        height: size.height,
        bitsPerComponent: 8,
        bytesPerRow: 0,
        space: CGColorSpace(name: CGColorSpace.sRGB)!,
        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
    ) else { throw HelperError(.recognitionFailed, "Das Bild laesst sich nicht drehen.") }
    // Coordinates with the origin at the bottom left.
    switch orientation {
    case .upright:
        break
    case .clockwise:
        // Straighten counterclockwise.
        context.translateBy(x: CGFloat(image.height), y: 0)
        context.rotate(by: .pi / 2)
    case .counterClockwise:
        context.translateBy(x: 0, y: CGFloat(image.width))
        context.rotate(by: -.pi / 2)
    case .upsideDown:
        context.translateBy(x: CGFloat(image.width), y: CGFloat(image.height))
        context.rotate(by: .pi)
    }
    context.interpolationQuality = .none
    context.draw(image.cgImage, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
    guard let rotated = context.makeImage() else {
        throw HelperError(.recognitionFailed, "Das Bild laesst sich nicht drehen.")
    }
    return InputImage(cgImage: rotated, luma: try grayscale(rotated))
}

private func run(
    handler: VNImageRequestHandler,
    region: CGRect,
    frame: PixelRect,
    image: InputImage,
    options: RecognizeOptions,
    source: Int,
    counter: inout Int
) throws -> (words: [RawWord], votes: [(TextOrientation, Int)]) {
    let request = VNRecognizeTextRequest()
    request.revision = VNRecognizeTextRequestRevision3
    request.recognitionLevel = .accurate
    request.recognitionLanguages = options.languages
    request.usesLanguageCorrection = options.languageCorrection
    request.minimumTextHeight = 0
    request.regionOfInterest = region
    do {
        try handler.perform([request])
    } catch {
        throw HelperError(.recognitionFailed, "Vision konnte das Bild nicht erkennen.")
    }
    var words: [RawWord] = []
    var votes: [(TextOrientation, Int)] = []
    for observation in request.results ?? [] {
        guard let candidate = observation.topCandidates(1).first else { continue }
        counter += 1
        // Reading direction of the line in pixels; Vision counts y upward.
        let dx = Double(observation.topRight.x - observation.topLeft.x) * Double(frame.width)
        let dy = -Double(observation.topRight.y - observation.topLeft.y) * Double(frame.height)
        votes.append((TextOrientation(dx: dx, dy: dy), candidate.string.count))
        for (range, token) in whitespaceTokens(candidate.string) {
            // No box, no word: without a box it cannot be painted over.
            guard let box = try? candidate.boundingBox(for: range)?.boundingBox else { continue }
            words.append(RawWord(
                text: latinized(token),
                rect: pixelRect(box, in: frame, imageWidth: image.width, imageHeight: image.height),
                confidence: Double(min(max(candidate.confidence, 0), 1)),
                source: source,
                observation: counter
            ))
        }
    }
    return (words, votes)
}

/// Cyrillic and Greek look-alikes of Latin letters.
let latinHomoglyphs: [Character: Character] = [
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "І": "I", "К": "K", "М": "M", "О": "O",
    "Р": "P", "Т": "T", "Х": "X", "У": "Y", "а": "a", "с": "c", "е": "e", "і": "i", "о": "o",
    "р": "p", "х": "x", "у": "y", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X", "ο": "o",
]

/// A word of Latin and Cyrillic or Greek letters is a reading error: only
/// de-DE and en-US are requested. Vision reads, for example, "АT61" with a
/// Cyrillic А, and the IBAN pattern would not match. Such words become
/// Latin; purely Cyrillic words stay as they are.
public func latinized(_ token: String) -> String {
    let latin = token.unicodeScalars.contains { $0.properties.isAlphabetic && $0.value < 0x0250 }
    let foreign = token.unicodeScalars.contains { (0x0370...0x04FF).contains($0.value) }
    guard latin, foreign else { return token }
    return String(token.map { latinHomoglyphs[$0] ?? $0 })
}

/// Splits at whitespace; punctuation stays on the word (``E-Mail:``,
/// ``shop@…``). ``enumerateSubstrings(.byWords)`` split email addresses at
/// ``@``, and those characters were then missing from the text.
public func whitespaceTokens(_ text: String) -> [(Range<String.Index>, String)] {
    var tokens: [(Range<String.Index>, String)] = []
    var start: String.Index?
    var index = text.startIndex
    while index < text.endIndex {
        let character = text[index]
        if character.isWhitespace {
            if let begin = start {
                tokens.append((begin..<index, String(text[begin..<index])))
                start = nil
            }
        } else if start == nil {
            start = index
        }
        index = text.index(after: index)
    }
    if let begin = start {
        tokens.append((begin..<text.endIndex, String(text[begin..<text.endIndex])))
    }
    return tokens
}

/// Tightens boxes only where they reach into the neighboring line.
///
/// If two words of different lines overlap, the gap is searched between
/// their ink masses: the rows without ink between the middle of the upper
/// word and the middle of the lower word, nearest to the band. The
/// overlapping edge is pulled back to the ink edge, at most to the middle
/// of that gap; both words keep half the line spacing. Without a gap
/// (glyphs touch), everything stays as it is: better to paint over a bit
/// too much than to leave a visible remnant of a glyph.
public func tighten(_ words: inout [OrderedWord], image: InputImage) {
    guard words.count > 1 else { return }
    for first in words.indices {
        for second in words.indices where second != first {
            let upper = words[first].word.rect
            let lower = words[second].word.rect
            guard (words[first].block, words[first].paragraph, words[first].line)
                != (words[second].block, words[second].paragraph, words[second].line),
                upper.centerY < lower.centerY,
                upper.horizontalOverlap(lower) > 0,
                upper.bottom > lower.top
            else { continue }
            // A band deeper than 60 % of a word is not a collision but a wrong line.
            guard (upper.bottom - lower.top) * 10 < min(upper.height, lower.height) * 6 else { continue }
            let span = upper.union(lower)
            guard let gap = gapBetweenLines(
                from: upper.top + upper.height / 3,
                to: lower.bottom - lower.height / 3,
                near: (lower.top + upper.bottom) / 2,
                left: span.left,
                right: span.right,
                image: image,
                reference: span
            ) else { continue }
            let middle = (gap.lowerBound + gap.upperBound) / 2
            if upper.bottom > middle, middle > upper.top {
                words[first].word.rect.height = middle - upper.top
            }
            if lower.top < middle, middle < lower.bottom {
                words[second].word.rect.top = middle
                words[second].word.rect.height = lower.bottom - middle
            }
        }
    }
}

/// The run of rows without ink between ``from`` and ``to`` that is nearest
/// to ``near``; ``nil`` if every row has ink.
func gapBetweenLines(
    from top: Int, to bottom: Int, near: Int, left: Int, right: Int, image: InputImage, reference: PixelRect
) -> Range<Int>? {
    guard bottom > top else { return nil }
    var darkest: UInt8 = 255
    var brightest: UInt8 = 0
    for y in reference.top..<reference.bottom {
        for x in reference.left..<reference.right {
            let value = image.lumaAt(x: x, y: y)
            darkest = min(darkest, value)
            brightest = max(brightest, value)
        }
    }
    guard Int(brightest) - Int(darkest) >= 48 else { return nil }
    let threshold = UInt8((Int(brightest) + Int(darkest)) / 2)
    func blank(_ y: Int) -> Bool {
        for x in left..<right where image.lumaAt(x: x, y: y) < threshold { return false }
        return true
    }
    var runs: [Range<Int>] = []
    var start: Int?
    for y in top..<bottom {
        if blank(y) {
            if start == nil { start = y }
        } else if let begin = start {
            runs.append(begin..<y)
            start = nil
        }
    }
    if let begin = start { runs.append(begin..<bottom) }
    func distance(_ run: Range<Int>) -> Int {
        run.contains(near) ? 0 : min(abs(run.lowerBound - near), abs(run.upperBound - 1 - near))
    }
    return runs.min { distance($0) < distance($1) }
}
