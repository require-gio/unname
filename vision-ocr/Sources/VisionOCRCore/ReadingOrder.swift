// Words → lines → blocks in reading order.
//
// Vision returns lines in no order across the page. From them this builds
// lines (one observation, reassembled across tile borders), blocks
// (recursive XY cut; columns are never interleaved) and
// ``line = [block, paragraph, line]``.

public struct OrderedWord: Equatable, Sendable {
    public var word: RawWord
    public var block: Int
    public var paragraph: Int
    public var line: Int
}

struct Line {
    var words: [RawWord]
    var rect: PixelRect

    init(words: [RawWord]) {
        self.words = words.sorted { $0.rect.left < $1.rect.left }
        self.rect = words.dropFirst().reduce(words[0].rect) { $0.union($1.rect) }
    }

    var height: Int { rect.height }
}

/// A vertical gap of at least this many line heights separates columns.
let columnGapFactor = 2.0
/// A horizontal gap of at least this many line heights separates blocks.
let blockGapFactor = 1.0
/// Within a block, a gap of at least this many line heights separates paragraphs.
let paragraphGapFactor = 0.6

public func readingOrder(_ words: [RawWord], pageWidth: Int) -> [OrderedWord] {
    guard !words.isEmpty else { return [] }
    let lines = buildLines(words, pageWidth: pageWidth)
    let unit = Double(median(lines.map(\.height)))
    let blocks = cut(lines, unit: unit)
    var ordered: [OrderedWord] = []
    for (blockIndex, block) in blocks.enumerated() {
        var paragraph = 1
        var lineInParagraph = 0
        var previous: Line?
        for line in block {
            if let previous {
                let gap = line.rect.top - previous.rect.bottom
                if Double(gap) >= paragraphGapFactor * unit {
                    paragraph += 1
                    lineInParagraph = 0
                }
            }
            lineInParagraph += 1
            for word in line.words {
                ordered.append(OrderedWord(word: word, block: blockIndex + 1, paragraph: paragraph, line: lineInParagraph))
            }
            previous = line
        }
    }
    return ordered
}

/// Lines from observations; fragments of the same line (tile border,
/// label with value) are put back together.
func buildLines(_ words: [RawWord], pageWidth: Int) -> [Line] {
    var groups: [Int: [RawWord]] = [:]
    var order: [Int] = []
    for word in words {
        if groups[word.observation] == nil { order.append(word.observation) }
        groups[word.observation, default: []].append(word)
    }
    var lines = order.map { Line(words: groups[$0]!) }
    var changed = true
    while changed {
        changed = false
        lines.sort { ($0.rect.left, $0.rect.top) < ($1.rect.left, $1.rect.top) }
        outer: for first in lines.indices {
            for second in lines.indices where second != first {
                if belongTogether(lines[first], lines[second], pageWidth: pageWidth) {
                    let joined = Line(words: lines[first].words + lines[second].words)
                    lines.remove(at: max(first, second))
                    lines[min(first, second)] = joined
                    changed = true
                    break outer
                }
            }
        }
    }
    return lines
}

/// Whether ``right`` continues ``left`` on the same baseline.
func belongTogether(_ left: Line, _ right: Line, pageWidth: Int) -> Bool {
    let smaller = min(left.height, right.height)
    guard smaller > 0, Double(left.rect.verticalOverlap(right.rect)) >= 0.6 * Double(smaller) else { return false }
    // Heights must match; otherwise a heading and body text would sit side by side.
    guard Double(max(left.height, right.height)) <= 1.8 * Double(smaller) else { return false }
    if left.rect.horizontalOverlap(right.rect) > 0 { return true }
    guard left.rect.left <= right.rect.left else { return false }
    let gap = right.rect.left - left.rect.right
    if Double(gap) <= 1.0 * Double(max(left.height, right.height)) { return true }
    // "Name:   Max Mustermann" – label and value stay one line, so the
    // context recognizers see them side by side.
    if let last = left.words.last?.text, last.hasSuffix(":"), Double(gap) <= 0.35 * Double(pageWidth) {
        return true
    }
    return false
}

/// Recursive XY cut. A vertical cut (columns) comes first so columns are
/// never interleaved; otherwise a horizontal gap separates.
func cut(_ lines: [Line], unit: Double) -> [[Line]] {
    guard lines.count > 1 else { return [lines] }
    if let x = verticalCut(lines, unit: unit) {
        let left = lines.filter { $0.rect.right <= x }
        let right = lines.filter { $0.rect.right > x }
        return cut(left, unit: unit) + cut(right, unit: unit)
    }
    if let y = horizontalCut(lines, unit: unit) {
        let upper = lines.filter { $0.rect.bottom <= y }
        let lower = lines.filter { $0.rect.bottom > y }
        return cut(upper, unit: unit) + cut(lower, unit: unit)
    }
    return [inRows(lines)]
}

/// Lines of a block row by row: whatever overlaps vertically sits in one
/// row and is read from left to right (table cells).
func inRows(_ lines: [Line]) -> [Line] {
    var rows: [(rect: PixelRect, lines: [Line])] = []
    for line in lines.sorted(by: { ($0.rect.top, $0.rect.left) < ($1.rect.top, $1.rect.left) }) {
        if let last = rows.last,
           Double(last.rect.verticalOverlap(line.rect)) >= 0.5 * Double(min(line.height, last.lines.map(\.height).min()!))
        {
            rows[rows.count - 1].rect = last.rect.union(line.rect)
            rows[rows.count - 1].lines.append(line)
        } else {
            rows.append((line.rect, [line]))
        }
    }
    return rows.flatMap { $0.lines.sorted { $0.rect.left < $1.rect.left } }
}

/// Middle of the widest vertical gap, if it qualifies as a column boundary.
func verticalCut(_ lines: [Line], unit: Double) -> Int? {
    guard let gap = widestGap(lines.map { ($0.rect.left, $0.rect.right) }),
          Double(gap.end - gap.start) >= columnGapFactor * unit
    else { return nil }
    let x = (gap.start + gap.end) / 2
    let left = lines.filter { $0.rect.right <= x }
    let right = lines.filter { $0.rect.right > x }
    // A table: at least three columns, almost every line of the smaller side
    // has a partner at the same height on the other side, and not both sides
    // are body text. Then it is read row by row. Two columns (address next to
    // sender) stay separate.
    let (smaller, larger) = left.count <= right.count ? (left, right) : (right, left)
    let columns = gaps(lines.map { ($0.rect.left, $0.rect.right) }, atLeast: Int(columnGapFactor * unit)) + 1
    if columns >= 3, alignedShare(smaller, larger) >= 0.7, !(isProse(left) && isProse(right)) {
        return nil
    }
    return x
}

func horizontalCut(_ lines: [Line], unit: Double) -> Int? {
    guard let gap = widestGap(lines.map { ($0.rect.top, $0.rect.bottom) }),
          Double(gap.end - gap.start) >= blockGapFactor * unit
    else { return nil }
    return (gap.start + gap.end) / 2
}

/// Widest gap between spans, only within their hull.
func widestGap(_ spans: [(Int, Int)]) -> (start: Int, end: Int)? {
    let sorted = spans.sorted { $0.0 < $1.0 }
    guard var reach = sorted.first?.1 else { return nil }
    var best: (start: Int, end: Int)?
    for span in sorted.dropFirst() {
        if span.0 > reach, span.0 - reach > (best.map { $0.end - $0.start } ?? 0) {
            best = (reach, span.0)
        }
        reach = max(reach, span.1)
    }
    return best
}

/// How many gaps of at least ``minimum`` lie between the spans.
func gaps(_ spans: [(Int, Int)], atLeast minimum: Int) -> Int {
    let sorted = spans.sorted { $0.0 < $1.0 }
    guard var reach = sorted.first?.1 else { return 0 }
    var count = 0
    for span in sorted.dropFirst() {
        if span.0 - reach >= minimum { count += 1 }
        reach = max(reach, span.1)
    }
    return count
}

func alignedShare(_ lines: [Line], _ others: [Line]) -> Double {
    guard !lines.isEmpty else { return 0 }
    let aligned = lines.filter { line in
        others.contains { other in
            Double(line.rect.verticalOverlap(other.rect)) >= 0.6 * Double(min(line.height, other.height))
        }
    }
    return Double(aligned.count) / Double(lines.count)
}

/// Body text: at least three lines, most of which almost fill the column.
func isProse(_ lines: [Line]) -> Bool {
    guard lines.count >= 3 else { return false }
    let left = lines.map(\.rect.left).min()!
    let right = lines.map(\.rect.right).max()!
    let span = Double(right - left)
    let full = lines.filter { Double($0.rect.width) >= 0.7 * span }
    return Double(full.count) >= 0.6 * Double(lines.count)
}

func median(_ values: [Int]) -> Int {
    guard !values.isEmpty else { return 1 }
    let sorted = values.sorted()
    return max(1, sorted[sorted.count / 2])
}
