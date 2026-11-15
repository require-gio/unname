// Stage 2: RecognizeDocumentsRequest (macOS 26 and later), behind
// ``--mode document``. Paragraphs and tables come from the document; every
// table cell is one line, and tables are read row by row. The stage is only
// switched on once the OCR gate confirms it.

import CoreGraphics
import Foundation
import Vision

// ``RecognizeDocumentsRequest`` exists only in the macOS 26 SDK (Swift 6.2,
// Xcode 26). With an older Xcode the helper builds without stage 2.
#if compiler(>=6.2)
public func documentRevisions() -> [Int] {
    guard #available(macOS 26, *) else { return [] }
    return RecognizeDocumentsRequest.supportedRevisions.map { revision in
        switch revision {
        case .revision1: 1
        @unknown default: 0
        }
    }.filter { $0 > 0 }
}

public func recognizeDocument(_ image: InputImage, options: RecognizeOptions) throws -> [OrderedWord] {
    guard #available(macOS 26, *) else {
        throw HelperError(.unsupported, "Die Dokumenterkennung braucht macOS 26.")
    }
    let box = ResultBox()
    let semaphore = DispatchSemaphore(value: 0)
    let cgImage = UncheckedImage(image: image.cgImage)
    let languages = options.languages
    let correction = options.languageCorrection
    Task.detached {
        do {
            var request = RecognizeDocumentsRequest(.revision1)
            request.textRecognitionOptions.recognitionLanguages = languages.map { Locale.Language(identifier: $0) }
            request.textRecognitionOptions.useLanguageCorrection = correction
            request.textRecognitionOptions.automaticallyDetectLanguage = false
            request.textRecognitionOptions.minimumTextHeightFraction = 0
            request.barcodeDetectionOptions.enabled = false
            box.set(.success(try await request.perform(on: cgImage.image)))
        } catch {
            box.set(.failure(error))
        }
        semaphore.signal()
    }
    semaphore.wait()
    let observations: [DocumentObservation]
    switch box.get() {
    case let .success(value): observations = value
    case .failure, .none: throw HelperError(.recognitionFailed, "Vision konnte das Dokument nicht erkennen.")
    }
    guard let document = observations.first?.document else { return [] }

    let full = PixelRect(left: 0, top: 0, width: image.width, height: image.height)
    var ordered: [OrderedWord] = []
    var block = 0
    var counter = 0
    let tableRects = document.tables.map { rect($0.boundingRegion.boundingBox.cgRect, image: image, frame: full) }

    func append(lines: [RecognizedTextObservation], block: Int, paragraph: Int, firstLine: Int) -> Int {
        var line = firstLine
        for observation in lines {
            guard let candidate = observation.topCandidates(1).first else { continue }
            counter += 1
            line += 1
            for (range, token) in whitespaceTokens(candidate.string) {
                guard let box = candidate.boundingBox(for: range)?.boundingBox.cgRect else { continue }
                ordered.append(OrderedWord(
                    word: RawWord(
                        text: latinized(token),
                        rect: rect(box, image: image, frame: full),
                        confidence: Double(min(max(observation.confidence, 0), 1)),
                        source: 0,
                        observation: counter
                    ),
                    block: block, paragraph: paragraph, line: line
                ))
            }
        }
        return line
    }

    // Paragraphs without the tables; those follow row by row at their position.
    var tablesDone = Set<Int>()
    for paragraph in document.paragraphs {
        let paragraphRect = rect(paragraph.boundingRegion.boundingBox.cgRect, image: image, frame: full)
        if let table = tableRects.firstIndex(where: { Double($0.intersectionArea(paragraphRect)) >= 0.5 * Double(paragraphRect.area) }) {
            if tablesDone.insert(table).inserted {
                block += 1
                for (rowIndex, row) in document.tables[table].rows.enumerated() {
                    var line = 0
                    for cell in row {
                        line = append(lines: cell.content.text.lines, block: block, paragraph: rowIndex + 1, firstLine: line)
                    }
                }
            }
            continue
        }
        block += 1
        _ = append(lines: paragraph.lines, block: block, paragraph: 1, firstLine: 0)
    }
    for (index, table) in document.tables.enumerated() where !tablesDone.contains(index) {
        block += 1
        for (rowIndex, row) in table.rows.enumerated() {
            var line = 0
            for cell in row {
                line = append(lines: cell.content.text.lines, block: block, paragraph: rowIndex + 1, firstLine: line)
            }
        }
    }
    tighten(&ordered, image: image)
    return ordered
}

private func rect(_ box: CGRect, image: InputImage, frame: PixelRect) -> PixelRect {
    pixelRect(box, in: frame, imageWidth: image.width, imageHeight: image.height)
}

private struct UncheckedImage: @unchecked Sendable {
    let image: CGImage
}

@available(macOS 26, *)
private final class ResultBox: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Result<[DocumentObservation], Error>?

    func set(_ result: Result<[DocumentObservation], Error>) {
        lock.lock()
        value = result
        lock.unlock()
    }

    func get() -> Result<[DocumentObservation], Error>? {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}
#else
public func documentRevisions() -> [Int] { [] }

public func recognizeDocument(_ image: InputImage, options: RecognizeOptions) throws -> [OrderedWord] {
    throw HelperError(.unsupported, "Die Dokumenterkennung braucht macOS 26.")
}
#endif
