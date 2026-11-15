import CoreGraphics
import CoreText
import Foundation
import XCTest

@testable import VisionOCRCore

final class ImageInputTests: XCTestCase {
    func testParsesPpmHeaderWithComment() throws {
        var data = Data("P6\n# Kommentar\n3 2\n255\n".utf8)
        data.append(Data(repeating: 255, count: 3 * 2 * 3))
        let header = try parseNetpbmHeader(data)
        XCTAssertEqual(header.channels, 3)
        XCTAssertEqual(header.width, 3)
        XCTAssertEqual(header.height, 2)
        XCTAssertEqual(header.dataOffset, data.count - 18)
    }

    func testRejectsTruncatedPixels() {
        var data = Data("P5 4 4 255\n".utf8)
        data.append(Data(repeating: 0, count: 15))
        XCTAssertThrowsError(try parseNetpbmHeader(data)) { error in
            XCTAssertEqual((error as? HelperError)?.code, .recognitionFailed)
        }
    }

    func testRejectsSixteenBitAndGarbage() {
        XCTAssertThrowsError(try parseNetpbmHeader(Data("P5 1 1 65535\n\0\0".utf8)))
        XCTAssertThrowsError(try parseNetpbmHeader(Data("P3 1 1 255\n0 0 0".utf8)))
        XCTAssertThrowsError(try loadImage(Data()))
        XCTAssertThrowsError(try loadImage(Data("kein Bild".utf8)))
    }

    func testRejectsTooManyPixelsBeforeReadingThem() {
        let data = Data("P5 20000 10000 255\n".utf8)
        XCTAssertThrowsError(try parseNetpbmHeader(data)) { error in
            XCTAssertEqual((error as? HelperError)?.code, .inputTooLarge)
        }
    }

    func testPgmLumaIsTakenAsIs() throws {
        var data = Data("P5 2 1 255\n".utf8)
        data.append(contentsOf: [10, 200])
        let image = try loadImage(data)
        XCTAssertEqual(image.width, 2)
        XCTAssertEqual(image.luma, [10, 200])
    }

    func testPpmLumaUsesBt601() throws {
        var data = Data("P6 1 1 255\n".utf8)
        data.append(contentsOf: [255, 0, 0])
        XCTAssertEqual(try loadImage(data).luma, [76])
    }
}

final class GeometryTests: XCTestCase {
    func testPixelRectFlipsOriginAndRespectsTile() {
        let tile = PixelRect(left: 100, top: 200, width: 400, height: 300)
        // Bottom left corner of the tile, a quarter wide, a third high.
        let box = CGRect(x: 0, y: 0, width: 0.25, height: 1.0 / 3)
        let rect = pixelRect(box, in: tile, imageWidth: 1000, imageHeight: 1000)
        XCTAssertEqual(rect, PixelRect(left: 100, top: 400, width: 100, height: 100))
    }

    func testPixelRectIsClampedAndAtLeastOnePixel() {
        let full = PixelRect(left: 0, top: 0, width: 50, height: 40)
        let outside = pixelRect(CGRect(x: 1.2, y: -0.5, width: 0.0, height: 0.0), in: full, imageWidth: 50, imageHeight: 40)
        XCTAssertEqual(outside.right, 50)
        XCTAssertEqual(outside.bottom, 40)
        XCTAssertGreaterThanOrEqual(outside.width, 1)
        XCTAssertGreaterThanOrEqual(outside.height, 1)
    }

    func testAutoGridFollowsTileSide() {
        XCTAssertEqual(tileGrid(width: 2480, height: 3509, spec: .auto).columns, 2)
        XCTAssertEqual(tileGrid(width: 2480, height: 3509, spec: .auto).rows, 2)
        // From 1000 px, at least two tiles per direction; below that, none.
        XCTAssertEqual(tileGrid(width: 1240, height: 1754, spec: .auto).columns, 2)
        XCTAssertEqual(tileGrid(width: 1240, height: 1754, spec: .auto).rows, 2)
        XCTAssertEqual(tileGrid(width: 800, height: 1600, spec: .auto).columns, 1)
        XCTAssertEqual(tileGrid(width: 800, height: 600, spec: .auto).rows, 1)
        XCTAssertEqual(tileGrid(width: 4000, height: 600, spec: .auto).columns, 3)
        XCTAssertEqual(tileGrid(width: 100, height: 100, spec: .grid(columns: 3, rows: 2)).columns, 3)
    }

    func testTilesOverlapAndKnowTheirInnerEdges() {
        let grid = tiles(width: 1000, height: 1000, columns: 2, rows: 2)
        XCTAssertEqual(grid.count, 4)
        let first = grid[0]
        XCTAssertEqual(first.rect.left, 0)
        XCTAssertEqual(first.rect.right, 525)
        XCTAssertFalse(first.innerLeft)
        XCTAssertTrue(first.innerRight)
        XCTAssertTrue(first.innerBottom)
        XCTAssertEqual(grid[3].rect.left, 475)
        XCTAssertTrue(tiles(width: 1000, height: 1000, columns: 1, rows: 1).isEmpty)
        XCTAssertTrue(first.touchesInnerEdge(PixelRect(left: 400, top: 10, width: 124, height: 20)))
        XCTAssertFalse(first.touchesInnerEdge(PixelRect(left: 0, top: 0, width: 100, height: 20)))
    }

    func testNormalizedRegionIsBottomLeft() {
        let region = normalizedRegion(PixelRect(left: 0, top: 0, width: 50, height: 25), imageWidth: 100, imageHeight: 100)
        XCTAssertEqual(region, CGRect(x: 0, y: 0.75, width: 0.5, height: 0.25))
    }
}

final class OrientationTests: XCTestCase {
    func testReadingDirectionGivesOrientation() {
        XCTAssertEqual(TextOrientation(dx: 10, dy: 1), .upright)
        XCTAssertEqual(TextOrientation(dx: -10, dy: 1), .upsideDown)
        XCTAssertEqual(TextOrientation(dx: 1, dy: 10), .clockwise)
        XCTAssertEqual(TextOrientation(dx: 1, dy: -10), .counterClockwise)
    }

    func testDominantOrientationNeedsSixtyPercent() {
        XCTAssertEqual(dominantOrientation([(.clockwise, 70), (.upright, 30)]), .clockwise)
        XCTAssertEqual(dominantOrientation([(.clockwise, 50), (.upright, 50)]), .upright)
        XCTAssertEqual(dominantOrientation([]), .upright)
    }

    /// A point in the original, straightened and mapped back, lands on itself.
    func testMapBackInvertsTheRotation() throws {
        let width = 40
        let height = 20
        var pixels = [UInt8](repeating: 255, count: width * height)
        // A black rectangle at the top left.
        for y in 2..<6 { for x in 3..<11 { pixels[y * width + x] = 0 } }
        var data = Data("P5 \(width) \(height) 255\n".utf8)
        data.append(contentsOf: pixels)
        let image = try loadImage(data)
        for orientation in [TextOrientation.clockwise, .counterClockwise, .upsideDown] {
            let upright = try rotateUpright(image, orientation)
            let size = orientation.uprightSize(width: width, height: height)
            XCTAssertEqual(upright.width, size.width)
            XCTAssertEqual(upright.height, size.height)
            // The ink in the upright image, as a box, mapped back.
            var left = Int.max, top = Int.max, right = 0, bottom = 0
            for y in 0..<upright.height {
                for x in 0..<upright.width where upright.lumaAt(x: x, y: y) < 128 {
                    left = min(left, x); top = min(top, y); right = max(right, x + 1); bottom = max(bottom, y + 1)
                }
            }
            let back = orientation.mapBack(PixelRect(left: left, top: top, right: right, bottom: bottom), width: width, height: height)
            XCTAssertEqual(back, PixelRect(left: 3, top: 2, right: 11, bottom: 6), "\(orientation)")
        }
    }
}

final class TokenTests: XCTestCase {
    func testSplitsOnWhitespaceOnlyAndKeepsPunctuation() {
        let tokens = whitespaceTokens("E-Mail: shop@moebelhaus.at  12/1/30\tEnde").map(\.1)
        XCTAssertEqual(tokens, ["E-Mail:", "shop@moebelhaus.at", "12/1/30", "Ende"])
    }

    func testMixedScriptWordsBecomeLatin() {
        XCTAssertEqual(latinized("АT61"), "AT61")
        XCTAssertEqual(latinized("<<EMAIL_сdf5b3e8>>"), "<<EMAIL_cdf5b3e8>>")
        XCTAssertEqual(latinized("Москва"), "Москва")
        XCTAssertEqual(latinized("Müller"), "Müller")
        XCTAssertEqual(latinized("1904"), "1904")
    }

    func testRangesPointIntoTheLine() {
        let line = " Max Mustermann "
        let tokens = whitespaceTokens(line)
        XCTAssertEqual(tokens.count, 2)
        XCTAssertEqual(String(line[tokens[1].0]), "Mustermann")
        XCTAssertTrue(whitespaceTokens("   ").isEmpty)
    }
}

private func word(_ text: String, _ left: Int, _ top: Int, _ width: Int = 100, _ height: Int = 20,
                  source: Int = 0, observation: Int, confidence: Double = 0.9) -> RawWord
{
    RawWord(text: text, rect: PixelRect(left: left, top: top, width: width, height: height),
            confidence: confidence, source: source, observation: observation)
}

final class MergeTests: XCTestCase {
    private let tile = Tile(rect: PixelRect(left: 0, top: 0, width: 525, height: 1000),
                            innerLeft: false, innerTop: false, innerRight: true, innerBottom: false)

    func testSameWordFromPageAndTileBecomesOneWithUnionBox() {
        let merged = mergeWords(
            page: [word("Mustermann", 100, 100, observation: 1)],
            tiles: [(tile, [word("MUSTERMANN", 104, 98, source: 1, observation: 2)])]
        )
        XCTAssertEqual(merged.count, 1)
        XCTAssertEqual(merged[0].rect, PixelRect(left: 100, top: 98, width: 104, height: 22))
    }

    func testTileReadingWinsAgainstPage() {
        let merged = mergeWords(
            page: [word("XI1234S67", 100, 100, observation: 1, confidence: 1.0)],
            tiles: [(tile, [word("XI1234567", 101, 100, source: 1, observation: 2, confidence: 0.5)])]
        )
        XCTAssertEqual(merged.map(\.text), ["XI1234567"])
    }

    func testBetweenTilesHigherConfidenceWins() {
        let other = Tile(rect: PixelRect(left: 475, top: 0, width: 525, height: 1000),
                         innerLeft: true, innerTop: false, innerRight: false, innerBottom: false)
        let merged = mergeWords(page: [], tiles: [
            // In the overlap strip, fully visible in both tiles.
            (tile, [word("4OK2AB", 480, 100, 40, source: 1, observation: 1, confidence: 0.4)]),
            (other, [word("4QK2AB", 480, 100, 40, source: 2, observation: 2, confidence: 0.8)]),
        ])
        XCTAssertEqual(merged.map(\.text), ["4QK2AB"])
    }

    func testWordsAtInnerEdgeAreDroppedAndPageFillsIn() {
        let merged = mergeWords(
            page: [word("Randwort", 480, 100, 90, observation: 1)],
            tiles: [(tile, [word("Rand", 480, 100, 45, source: 1, observation: 2)])]
        )
        XCTAssertEqual(merged.map(\.text), ["Randwort"])
    }

    func testPageWordCoveredByTileWordsDisappears() {
        let merged = mergeWords(
            page: [word("E-Mail:shop@x.at", 100, 100, 200, observation: 1)],
            tiles: [(tile, [
                word("E-Mail:", 100, 100, 80, source: 1, observation: 2),
                word("shop@x.at", 190, 100, 110, source: 1, observation: 2),
            ])]
        )
        XCTAssertEqual(merged.map(\.text), ["E-Mail:", "shop@x.at"])
    }

    func testDistinctWordsStay() {
        let merged = mergeWords(page: [word("Max", 100, 100, observation: 1), word("Wien", 100, 400, observation: 2)], tiles: [])
        XCTAssertEqual(merged.count, 2)
    }
}

final class ReadingOrderTests: XCTestCase {
    private func text(_ ordered: [OrderedWord]) -> [String] {
        var lines: [String] = []
        var previous: [Int]?
        for item in ordered {
            let key = [item.block, item.paragraph, item.line]
            if key == previous { lines[lines.count - 1] += " " + item.word.text } else { lines.append(item.word.text) }
            previous = key
        }
        return lines
    }

    func testTwoColumnsAreNotInterleaved() {
        var words: [RawWord] = []
        for row in 0..<5 {
            words.append(word("links\(row) aaaa bbbb", 100, 100 + row * 30, 800, observation: row + 1))
            words.append(word("rechts\(row) cccc dddd", 1100, 100 + row * 30, 800, observation: row + 11))
        }
        let lines = text(readingOrder(words.shuffled(), pageWidth: 2000))
        XCTAssertEqual(lines.prefix(5).map { String($0.prefix(6)) }, ["links0", "links1", "links2", "links3", "links4"])
        XCTAssertTrue(lines[5].hasPrefix("rechts0"))
    }

    func testTableIsReadRowByRow() {
        var words: [RawWord] = []
        var observation = 0
        for row in 0..<3 {
            for column in 0..<4 {
                observation += 1
                // Header line slightly offset, as in the scan.
                words.append(word("r\(row)c\(column)", 100 + column * 400, 100 + row * 40 + (column % 2) * 3, 120, observation: observation))
            }
        }
        let lines = text(readingOrder(words.shuffled(), pageWidth: 2000))
        XCTAssertEqual(lines.prefix(5), ["r0c0", "r0c1", "r0c2", "r0c3", "r1c0"])
    }

    func testLabelAndValueStayOneLine() {
        let words = [
            word("Name:", 100, 100, 120, observation: 1),
            word("Max", 600, 102, 80, observation: 2),
            word("Mustermann", 690, 102, 200, observation: 2),
        ]
        let ordered = readingOrder(words, pageWidth: 2000)
        XCTAssertEqual(text(ordered), ["Name: Max Mustermann"])
    }

    func testLineSplitAtTileBorderIsJoined() {
        let words = [
            word("Moebelhaus", 100, 100, 200, source: 1, observation: 1),
            word("Online", 310, 101, 120, source: 2, observation: 2),
        ]
        XCTAssertEqual(text(readingOrder(words, pageWidth: 2000)), ["Moebelhaus Online"])
    }

    func testBlocksFollowVerticalWhitespace() {
        let words = [
            word("Kopf", 100, 100, observation: 1),
            word("Text", 100, 300, observation: 2),
        ]
        let ordered = readingOrder(words, pageWidth: 2000)
        XCTAssertEqual(ordered.map(\.block), [1, 2])
    }
}

final class TightenTests: XCTestCase {
    /// White with two black bars (lines) and an empty line between them.
    private func image() throws -> InputImage {
        let width = 100
        let height = 60
        var pixels = [UInt8](repeating: 255, count: width * height)
        for y in 10..<28 { for x in 10..<90 { pixels[y * width + x] = 0 } }
        for y in 32..<50 { for x in 10..<90 { pixels[y * width + x] = 0 } }
        var data = Data("P5 \(width) \(height) 255\n".utf8)
        data.append(contentsOf: pixels)
        return try loadImage(data)
    }

    func testOverlapIsRemovedDownToTheGapButNotIntoInk() throws {
        let upper = OrderedWord(word: word("oben", 10, 8, 80, 26, observation: 1), block: 1, paragraph: 1, line: 1)
        let lower = OrderedWord(word: word("unten", 10, 26, 80, 26, observation: 2), block: 1, paragraph: 1, line: 2)
        var words = [upper, lower]
        try tighten(&words, image: image())
        XCTAssertEqual(words[0].word.rect.bottom, 30)
        XCTAssertEqual(words[1].word.rect.top, 30)
        XCTAssertGreaterThanOrEqual(words[0].word.rect.bottom, 28)
        XCTAssertLessThanOrEqual(words[1].word.rect.top, 32)
    }

    /// The upper box reaches into the ascender area of the lower line; the band
    /// itself holds only ink – the gap lies above it (OCR gate: "von" (from) above "fuer" (for)).
    func testOverlapIntoAscendersIsRemovedAtTheGapAbove() throws {
        let width = 100
        let height = 60
        var pixels = [UInt8](repeating: 255, count: width * height)
        for y in 10..<24 { for x in 10..<90 { pixels[y * width + x] = 0 } }
        for y in 32..<50 { for x in 10..<90 { pixels[y * width + x] = 0 } }
        var data = Data("P5 \(width) \(height) 255\n".utf8)
        data.append(contentsOf: pixels)
        let image = try loadImage(data)
        let upper = OrderedWord(word: word("von", 10, 8, 80, 26, observation: 1), block: 1, paragraph: 1, line: 1)
        let lower = OrderedWord(word: word("fuer", 10, 32, 80, 20, observation: 2), block: 1, paragraph: 1, line: 2)
        var words = [upper, lower]
        tighten(&words, image: image)
        XCTAssertEqual(words[0].word.rect.bottom, 28)
        XCTAssertEqual(words[1].word.rect.top, 32, "the lower box stays at its ink")
    }

    func testNoChangeWithoutCollision() throws {
        let upper = OrderedWord(word: word("oben", 10, 8, 80, 21, observation: 1), block: 1, paragraph: 1, line: 1)
        var words = [upper]
        try tighten(&words, image: image())
        XCTAssertEqual(words[0].word.rect, upper.word.rect)
    }
}

final class ArgumentTests: XCTestCase {
    func testParsesOptions() throws {
        let options = try parseRecognizeArguments(["--languages", "de-DE,en-US", "--tiles", "2x2", "--correction", "on", "--qos", "utility"])
        XCTAssertEqual(options.languages, ["de-DE", "en-US"])
        XCTAssertEqual(options.tiles, .grid(columns: 2, rows: 2))
        XCTAssertTrue(options.languageCorrection)
        XCTAssertEqual(options.qos, .utility)
    }

    func testRejectsUnknownAndMissing() {
        XCTAssertThrowsError(try parseRecognizeArguments([]))
        XCTAssertThrowsError(try parseRecognizeArguments(["--languages", "de-DE", "--tiles", "4x4"]))
        XCTAssertThrowsError(try parseRecognizeArguments(["--languages", "de-DE", "--frei"]))
        XCTAssertThrowsError(try parseRecognizeArguments(["--languages"]))
    }

    func testResponseKeysMatchProtocol() throws {
        let response = RecognizeResponse(request: "text", revision: 3, osBuild: "26B123", width: 1, height: 1, tiles: "1x1", elapsedMs: 1, words: [])
        let json = try JSONSerialization.jsonObject(with: encodeLine(response)) as? [String: Any]
        XCTAssertEqual(Set(json?.keys ?? [:].keys), ["protocol", "request", "revision", "os_build", "width", "height", "tiles", "elapsed_ms", "words"])
    }
}

/// Integration: render text into an image with CoreText and read it with Vision.
final class RecognitionIntegrationTests: XCTestCase {
    private func render(_ lines: [String], size: CGFloat = 48, width: Int = 1600, height: Int = 600) throws -> Data {
        let space = CGColorSpaceCreateDeviceGray()
        guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width,
                                      space: space, bitmapInfo: CGImageAlphaInfo.none.rawValue)
        else { throw XCTSkip("Kein Grafikkontext.") }
        context.setFillColor(gray: 1, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        let font = CTFontCreateWithName("Helvetica" as CFString, size, nil)
        for (index, line) in lines.enumerated() {
            let attributed = NSAttributedString(string: line, attributes: [
                NSAttributedString.Key(kCTFontAttributeName as String): font,
                NSAttributedString.Key(kCTForegroundColorAttributeName as String): CGColor(gray: 0, alpha: 1),
            ])
            context.textPosition = CGPoint(x: 60, y: CGFloat(height) - 100 - CGFloat(index) * size * 1.6)
            CTLineDraw(CTLineCreateWithAttributedString(attributed), context)
        }
        guard let image = context.makeImage() else { throw XCTSkip("Kein Bild.") }
        var data = Data("P5 \(width) \(height) 255\n".utf8)
        data.append(contentsOf: try grayscale(image))
        return data
    }

    func testRecognizesRenderedTextWithBoxesInsideTheImage() throws {
        guard ProcessInfo.processInfo.environment["VISION_OCR_INTEGRATION"] == "1" else {
            throw XCTSkip("Set VISION_OCR_INTEGRATION=1; the first run compiles for ≈ 50 s.")
        }
        let data = try render(["Max Mustermann", "E-Mail: max@beispiel.at", "IBAN AT61 1904 3002 3457 3201"])
        var options = RecognizeOptions(languages: ["de-DE", "en-US"])
        options.tiles = .grid(columns: 2, rows: 1)
        let response = try recognize(data, options: options)
        let texts = response.words.map(\.text)
        XCTAssertTrue(texts.contains("Mustermann"), "\(texts)")
        XCTAssertTrue(texts.contains("max@beispiel.at"), "\(texts)")
        XCTAssertEqual(response.revision, 3)
        for word in response.words {
            XCTAssertGreaterThanOrEqual(word.left, 0)
            XCTAssertLessThanOrEqual(word.left + word.width, response.width)
            XCTAssertLessThanOrEqual(word.top + word.height, response.height)
        }
        // Reading order: first line first.
        XCTAssertEqual(texts.first, "Max")
    }
}
