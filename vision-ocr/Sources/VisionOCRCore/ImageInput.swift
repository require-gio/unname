// Image from stdin: PPM (P6) and PGM (P5) without decoding, PNG/TIFF/JPEG
// through ImageIO.

import CoreGraphics
import Foundation
import ImageIO

/// No larger input is accepted (exit 3).
public let maxInputBytes = 256 * 1024 * 1024
/// Same as ``max_image_pixels`` in the extractor.
public let maxImagePixels = 100_000_000

/// An image plus a gray value per pixel; tightening needs the gray values.
public struct InputImage: @unchecked Sendable {
    public let cgImage: CGImage
    public let width: Int
    public let height: Int
    /// Row by row, one byte per pixel, 0 = black.
    public let luma: [UInt8]

    public init(cgImage: CGImage, luma: [UInt8]) {
        self.cgImage = cgImage
        self.width = cgImage.width
        self.height = cgImage.height
        self.luma = luma
    }

    public func lumaAt(x: Int, y: Int) -> UInt8 {
        luma[y * width + x]
    }
}

public struct NetpbmHeader: Equatable, Sendable {
    public let channels: Int
    public let width: Int
    public let height: Int
    /// First byte of the pixels.
    public let dataOffset: Int
}

/// Reads the header of P5/P6 with ``maxval`` 255. Comments (``#``) are allowed.
public func parseNetpbmHeader(_ data: Data) throws -> NetpbmHeader {
    let bytes = [UInt8](data.prefix(4096))
    guard bytes.count >= 2, bytes[0] == UInt8(ascii: "P"),
          bytes[1] == UInt8(ascii: "5") || bytes[1] == UInt8(ascii: "6")
    else { throw HelperError(.recognitionFailed, "Kein PPM/PGM-Bild.") }
    let channels = bytes[1] == UInt8(ascii: "6") ? 3 : 1
    var index = 2
    var fields: [Int] = []
    while fields.count < 3 {
        // Skip whitespace and comments.
        while index < bytes.count {
            if bytes[index] == UInt8(ascii: "#") {
                while index < bytes.count, bytes[index] != 0x0A { index += 1 }
            } else if isWhitespace(bytes[index]) {
                index += 1
            } else {
                break
            }
        }
        var value = 0
        var digits = 0
        while index < bytes.count, (48...57).contains(bytes[index]) {
            value = value * 10 + Int(bytes[index] - 48)
            digits += 1
            index += 1
            guard digits <= 9 else { throw HelperError(.recognitionFailed, "PPM-Kopf ist ungueltig.") }
        }
        guard digits > 0, index < bytes.count else {
            throw HelperError(.recognitionFailed, "PPM-Kopf ist ungueltig.")
        }
        fields.append(value)
    }
    // Exactly one space separates header and pixels.
    guard isWhitespace(bytes[index]) else { throw HelperError(.recognitionFailed, "PPM-Kopf ist ungueltig.") }
    let (width, height, maxValue) = (fields[0], fields[1], fields[2])
    guard maxValue == 255 else { throw HelperError(.recognitionFailed, "Nur 8 Bit je Kanal.") }
    guard width >= 1, height >= 1 else { throw HelperError(.recognitionFailed, "Das Bild ist leer.") }
    guard width * height <= maxImagePixels else { throw HelperError(.inputTooLarge, "Das Bild hat zu viele Pixel.") }
    let offset = index + 1
    guard data.count - offset == width * height * channels else {
        throw HelperError(.recognitionFailed, "Die Pixel passen nicht zum PPM-Kopf.")
    }
    return NetpbmHeader(channels: channels, width: width, height: height, dataOffset: offset)
}

private func isWhitespace(_ byte: UInt8) -> Bool {
    byte == 0x20 || byte == 0x09 || byte == 0x0A || byte == 0x0D
}

public func loadImage(_ data: Data) throws -> InputImage {
    guard data.count <= maxInputBytes else { throw HelperError(.inputTooLarge, "Die Eingabe ist zu gross.") }
    guard !data.isEmpty else { throw HelperError(.recognitionFailed, "Kein Bild auf stdin.") }
    if data.first == UInt8(ascii: "P") {
        return try loadNetpbm(data)
    }
    return try loadEncoded(data)
}

private func loadNetpbm(_ data: Data) throws -> InputImage {
    let header = try parseNetpbmHeader(data)
    let pixels = data.subdata(in: (data.startIndex + header.dataOffset)..<data.endIndex)
    guard let provider = CGDataProvider(data: pixels as CFData) else {
        throw HelperError(.recognitionFailed, "Das Bild laesst sich nicht uebernehmen.")
    }
    let rgb = header.channels == 3
    guard let image = CGImage(
        width: header.width,
        height: header.height,
        bitsPerComponent: 8,
        bitsPerPixel: 8 * header.channels,
        bytesPerRow: header.width * header.channels,
        space: rgb ? CGColorSpace(name: CGColorSpace.sRGB)! : CGColorSpaceCreateDeviceGray(),
        bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
        provider: provider,
        decode: nil,
        shouldInterpolate: false,
        intent: .defaultIntent
    ) else { throw HelperError(.recognitionFailed, "Das Bild laesst sich nicht uebernehmen.") }

    var luma = [UInt8](repeating: 0, count: header.width * header.height)
    pixels.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
        let source = raw.bindMemory(to: UInt8.self)
        if rgb {
            for index in 0..<luma.count {
                let base = index * 3
                // ITU-R BT.601, integer arithmetic.
                let value = 299 * Int(source[base]) + 587 * Int(source[base + 1]) + 114 * Int(source[base + 2])
                luma[index] = UInt8(value / 1000)
            }
        } else {
            for index in 0..<luma.count { luma[index] = source[index] }
        }
    }
    return InputImage(cgImage: image, luma: luma)
}

private func loadEncoded(_ data: Data) throws -> InputImage {
    let options = [kCGImageSourceShouldCache: false] as CFDictionary
    guard let source = CGImageSourceCreateWithData(data as CFData, options),
          let type = CGImageSourceGetType(source) as String?,
          ["public.png", "public.tiff", "public.jpeg"].contains(type),
          let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let width = properties[kCGImagePropertyPixelWidth] as? Int,
          let height = properties[kCGImagePropertyPixelHeight] as? Int
    else { throw HelperError(.recognitionFailed, "Das Bildformat wird nicht unterstuetzt.") }
    // Before decoding: no image over the limit ends up in memory.
    guard width * height <= maxImagePixels else { throw HelperError(.inputTooLarge, "Das Bild hat zu viele Pixel.") }
    guard let image = CGImageSourceCreateImageAtIndex(source, 0, options) else {
        throw HelperError(.recognitionFailed, "Das Bild laesst sich nicht dekodieren.")
    }
    return InputImage(cgImage: image, luma: try grayscale(image))
}

/// Gray values of any image; the sidecar has already applied the EXIF
/// orientation.
func grayscale(_ image: CGImage) throws -> [UInt8] {
    var luma = [UInt8](repeating: 255, count: image.width * image.height)
    let drawn = luma.withUnsafeMutableBytes { buffer -> Bool in
        guard let context = CGContext(
            data: buffer.baseAddress,
            width: image.width,
            height: image.height,
            bitsPerComponent: 8,
            bytesPerRow: image.width,
            space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        ) else { return false }
        context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        return true
    }
    guard drawn else { throw HelperError(.recognitionFailed, "Das Bild laesst sich nicht lesen.") }
    return luma
}
