// Merge the whole page and the tiles into one word list.

/// A word before reading order.
public struct RawWord: Equatable, Sendable {
    public var text: String
    public var rect: PixelRect
    public var confidence: Double
    /// ``0`` is the whole page, ``1…n`` are the tiles.
    public var source: Int
    /// Unique per Vision observation (line) across all sources.
    public var observation: Int

    public init(text: String, rect: PixelRect, confidence: Double, source: Int, observation: Int) {
        self.text = text
        self.rect = rect
        self.confidence = confidence
        self.source = source
        self.observation = observation
    }

    var isFromTile: Bool { source > 0 }
}

/// From this IoU, two readings count as the same word.
public let sameWordIoU = 0.5
/// From this share of its area covered by tile words, a whole-page word is
/// dropped: the tile split the spot differently, for example reading
/// ``E-Mail:shop@…`` as two words.
public let coveredShare = 0.6

/// Merges tile words and page words.
///
/// - Words at an inner tile edge are dropped; the neighboring tile (or the
///   whole page) sees them whole.
/// - Same text (casefolded) at IoU ≥ 0.5 gives one word with the union of
///   both boxes.
/// - Different text at IoU ≥ 0.5: the tile beats the page (higher
///   resolution), otherwise the higher confidence wins.
public func mergeWords(page: [RawWord], tiles: [(tile: Tile, words: [RawWord])]) -> [RawWord] {
    var merged: [RawWord] = []
    for (tile, words) in tiles {
        for word in words where !tile.touchesInnerEdge(word.rect) {
            absorb(word, into: &merged)
        }
    }
    let tileWords = merged
    for word in page {
        let covered = tileWords.reduce(0) { $0 + word.rect.intersectionArea($1.rect) }
        if !tileWords.isEmpty, Double(covered) >= coveredShare * Double(word.rect.area),
           !tileWords.contains(where: { $0.rect.iou(word.rect) >= sameWordIoU && sameText($0, word) })
        {
            continue
        }
        absorb(word, into: &merged)
    }
    return merged
}

private func absorb(_ word: RawWord, into merged: inout [RawWord]) {
    guard let index = merged.firstIndex(where: { $0.rect.iou(word.rect) >= sameWordIoU }) else {
        merged.append(word)
        return
    }
    var kept = merged[index]
    if sameText(kept, word) {
        kept.rect = kept.rect.union(word.rect)
        kept.confidence = max(kept.confidence, word.confidence)
        merged[index] = kept
        return
    }
    if kept.isFromTile != word.isFromTile {
        if word.isFromTile { merged[index] = word }
        return
    }
    if word.confidence > kept.confidence { merged[index] = word }
}

private func sameText(_ first: RawWord, _ second: RawWord) -> Bool {
    first.text.lowercased() == second.text.lowercased()
}
