// unname-vision-ocr: invocation and exit codes.
//
//   unname-vision-ocr info
//   unname-vision-ocr recognize --languages de-DE,en-US [--tiles auto|1x1|2x2|3x3]
//       [--mode text|document] [--correction off|on] [--qos user-initiated]
//
// Image on stdin (PPM/PGM, PNG, TIFF, JPEG), one JSON line on stdout.
// Messages on stderr never name content.

import Darwin
import Foundation
import VisionOCRCore

func fail(_ error: HelperError) -> Never {
    FileHandle.standardError.write(Data("unname-vision-ocr: \(error.message)\n".utf8))
    exit(error.code.rawValue)
}

func write(_ data: Data) {
    FileHandle.standardOutput.write(data)
}

/// Reads stdin to EOF; fails once the input exceeds the size limit.
func readInput() throws -> Data {
    var data = Data()
    let handle = FileHandle.standardInput
    while true {
        let chunk = try handle.read(upToCount: 4 * 1024 * 1024) ?? Data()
        if chunk.isEmpty { break }
        data.append(chunk)
        if data.count > maxInputBytes { throw HelperError(.inputTooLarge, "Die Eingabe ist zu gross.") }
    }
    return data
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard let command = arguments.first else {
    fail(HelperError(.usage, "Befehl fehlt (info, recognize)."))
}

do {
    switch command {
    case "info":
        guard arguments.count == 1 else { throw HelperError(.usage, "info nimmt keine Optionen.") }
        write(try encodeLine(try info()))
    case "recognize":
        let options = try parseRecognizeArguments(Array(arguments.dropFirst()))
        pthread_set_qos_class_self_np(options.qos.qosClass, 0)
        let input: Data
        do {
            input = try readInput()
        } catch let error as HelperError {
            throw error
        } catch {
            throw HelperError(.recognitionFailed, "stdin laesst sich nicht lesen.")
        }
        write(try encodeLine(try recognize(input, options: options)))
    default:
        throw HelperError(.usage, "Unbekannter Befehl.")
    }
} catch let error as HelperError {
    fail(error)
} catch {
    fail(HelperError(.recognitionFailed, "Unerwarteter Fehler."))
}
exit(0)
