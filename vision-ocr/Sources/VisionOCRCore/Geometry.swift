// Coordinates: Vision returns normalized boxes with the origin at the bottom
// left, relative to the tile. The sidecar expects integer pixels with the
// origin at the top left.

import CoreGraphics

public struct PixelRect: Equatable, Hashable, Sendable {
    public var left: Int
    public var top: Int
    public var width: Int
    public var height: Int

    public init(left: Int, top: Int, width: Int, height: Int) {
        self.left = left
        self.top = top
        self.width = width
        self.height = height
    }

    public init(left: Int, top: Int, right: Int, bottom: Int) {
        self.init(left: left, top: top, width: right - left, height: bottom - top)
    }

    public var right: Int { left + width }
    public var bottom: Int { top + height }
    public var area: Int { max(0, width) * max(0, height) }
    public var centerY: Double { Double(top) + Double(height) / 2 }

    public func intersectionArea(_ other: PixelRect) -> Int {
        let width = min(right, other.right) - max(left, other.left)
        let height = min(bottom, other.bottom) - max(top, other.top)
        return width > 0 && height > 0 ? width * height : 0
    }

    public func iou(_ other: PixelRect) -> Double {
        let shared = intersectionArea(other)
        guard shared > 0 else { return 0 }
        return Double(shared) / Double(area + other.area - shared)
    }

    public func union(_ other: PixelRect) -> PixelRect {
        PixelRect(
            left: min(left, other.left),
            top: min(top, other.top),
            right: max(right, other.right),
            bottom: max(bottom, other.bottom)
        )
    }

    /// Overlap of the vertical spans, in pixels.
    public func verticalOverlap(_ other: PixelRect) -> Int {
        max(0, min(bottom, other.bottom) - max(top, other.top))
    }

    public func horizontalOverlap(_ other: PixelRect) -> Int {
        max(0, min(right, other.right) - max(left, other.left))
    }
}

/// A tile in page pixels, plus the edges that lie inside the image (inner edges).
public struct Tile: Equatable, Sendable {
    public let rect: PixelRect
    public let innerLeft: Bool
    public let innerTop: Bool
    public let innerRight: Bool
    public let innerBottom: Bool

    /// Whether a box touches an inner edge; the neighboring tile sees it whole.
    public func touchesInnerEdge(_ box: PixelRect, margin: Int = 2) -> Bool {
        (innerLeft && box.left <= rect.left + margin)
            || (innerTop && box.top <= rect.top + margin)
            || (innerRight && box.right >= rect.right - margin)
            || (innerBottom && box.bottom >= rect.bottom - margin)
    }
}

/// The most pixels a tile side may have in the ``auto`` grid.
public let autoTileSide = 1_900
/// From this edge length, ``auto`` splits into at least two tiles. Vision
/// sometimes silently skips a whole page (OCR gate: a table at 150 dpi, 0 of
/// 77 words) but reads its halves; small print at low resolution gains the
/// same way. PII word recall in the gate: 0.896 → 0.970.
public let autoTileSplitSide = 1_000
/// Share of the tile size by which neighbors overlap.
public let tileOverlap = 0.10

public func tileGrid(width: Int, height: Int, spec: TileSpec) -> (columns: Int, rows: Int) {
    switch spec {
    case .auto:
        let columns = max(width >= autoTileSplitSide ? 2 : 1, (width + autoTileSide - 1) / autoTileSide)
        let rows = max(height >= autoTileSplitSide ? 2 : 1, (height + autoTileSide - 1) / autoTileSide)
        return (columns, rows)
    case let .grid(columns, rows):
        return (columns, rows)
    }
}

/// Tiles of a grid; none for 1×1 (the whole page is enough).
public func tiles(width: Int, height: Int, columns: Int, rows: Int) -> [Tile] {
    guard columns * rows > 1 else { return [] }
    let cellWidth = Double(width) / Double(columns)
    let cellHeight = Double(height) / Double(rows)
    let padX = Int((cellWidth * tileOverlap / 2).rounded(.up))
    let padY = Int((cellHeight * tileOverlap / 2).rounded(.up))
    var result: [Tile] = []
    for row in 0..<rows {
        for column in 0..<columns {
            let left = max(0, Int((Double(column) * cellWidth).rounded()) - padX)
            let right = min(width, Int((Double(column + 1) * cellWidth).rounded()) + padX)
            let top = max(0, Int((Double(row) * cellHeight).rounded()) - padY)
            let bottom = min(height, Int((Double(row + 1) * cellHeight).rounded()) + padY)
            result.append(Tile(
                rect: PixelRect(left: left, top: top, right: right, bottom: bottom),
                innerLeft: left > 0,
                innerTop: top > 0,
                innerRight: right < width,
                innerBottom: bottom < height
            ))
        }
    }
    return result
}

/// Normalized region (origin at the bottom left) of a tile within the whole image.
public func normalizedRegion(_ tile: PixelRect, imageWidth: Int, imageHeight: Int) -> CGRect {
    CGRect(
        x: Double(tile.left) / Double(imageWidth),
        y: Double(imageHeight - tile.bottom) / Double(imageHeight),
        width: Double(tile.width) / Double(imageWidth),
        height: Double(tile.height) / Double(imageHeight)
    )
}

/// Normalized box relative to ``frame`` → pixels in the image, clamped, at least 1 px.
public func pixelRect(_ box: CGRect, in frame: PixelRect, imageWidth: Int, imageHeight: Int) -> PixelRect {
    let x0 = Double(frame.left) + box.minX * Double(frame.width)
    let x1 = Double(frame.left) + box.maxX * Double(frame.width)
    // Origin at the bottom left: maxY is the top edge.
    let y0 = Double(frame.top) + (1 - box.maxY) * Double(frame.height)
    let y1 = Double(frame.top) + (1 - box.minY) * Double(frame.height)
    var left = Int(x0.rounded(.down))
    var top = Int(y0.rounded(.down))
    var right = Int(x1.rounded(.up))
    var bottom = Int(y1.rounded(.up))
    left = min(max(left, 0), imageWidth - 1)
    top = min(max(top, 0), imageHeight - 1)
    right = min(max(right, left + 1), imageWidth)
    bottom = min(max(bottom, top + 1), imageHeight)
    return PixelRect(left: left, top: top, right: right, bottom: bottom)
}

/// Orientation of the text in the image. Vision reads rotated text but then
/// returns vertical line boxes; line building and reading order need
/// horizontal text. So a rotated image is recognized upright and every box
/// is mapped back.
public enum TextOrientation: Sendable, Equatable {
    case upright
    /// Text runs from top to bottom (rotated 90° clockwise).
    case clockwise
    /// Text runs from bottom to top.
    case counterClockwise
    case upsideDown

    /// From the reading direction of a line in pixels (y pointing down).
    public init(dx: Double, dy: Double) {
        if abs(dx) >= abs(dy) {
            self = dx >= 0 ? .upright : .upsideDown
        } else {
            self = dy > 0 ? .clockwise : .counterClockwise
        }
    }

    /// Size of the upright image.
    public func uprightSize(width: Int, height: Int) -> (width: Int, height: Int) {
        switch self {
        case .upright, .upsideDown: (width, height)
        case .clockwise, .counterClockwise: (height, width)
        }
    }

    /// Box in the upright image → box in the original image (``width``/``height``
    /// of the original).
    public func mapBack(_ rect: PixelRect, width: Int, height: Int) -> PixelRect {
        switch self {
        case .upright:
            return rect
        case .upsideDown:
            return PixelRect(left: width - rect.right, top: height - rect.bottom, right: width - rect.left, bottom: height - rect.top)
        case .clockwise:
            // Straightened counterclockwise: (x, y) → (y, W − x).
            return PixelRect(left: width - rect.bottom, top: rect.left, right: width - rect.top, bottom: rect.right)
        case .counterClockwise:
            // Straightened clockwise: (x, y) → (H − y, x).
            return PixelRect(left: rect.top, top: height - rect.right, right: rect.bottom, bottom: height - rect.left)
        }
    }
}

/// The dominant orientation, weighted by text length; upright unless no
/// other orientation carries at least 60 % of the text.
public func dominantOrientation(_ votes: [(TextOrientation, Int)]) -> TextOrientation {
    let total = votes.reduce(0) { $0 + $1.1 }
    guard total > 0 else { return .upright }
    var weights: [TextOrientation: Int] = [:]
    for (orientation, weight) in votes { weights[orientation, default: 0] += weight }
    guard let (best, weight) = weights.max(by: { $0.value < $1.value }), best != .upright,
          Double(weight) >= 0.6 * Double(total)
    else { return .upright }
    return best
}

extension TextOrientation: Hashable {}
