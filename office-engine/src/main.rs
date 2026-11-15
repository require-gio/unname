//! Narrow interface of the office engine:
//!
//! - `unname-office-engine info` writes one JSON line with the engine, the
//!   engine version and the protocol to stdout.
//! - `unname-office-engine docx-to-pdf [--fonts DIR]...` reads a DOCX from
//!   stdin and writes the PDF to stdout.
//!
//! No files, no network, no paths from the document: the sidecar passes bytes
//! in and gets bytes back. Error messages on stderr name only the kind of
//! error, never content of the document. Exit codes: 0 success, 2 bad usage,
//! 3 input too large, 4 conversion failed.

use std::io::{self, Read, Write};
use std::path::PathBuf;
use std::process::ExitCode;

#[cfg(all(feature = "office2pdf", feature = "docxide"))]
compile_error!("Exactly one engine per build: feature `office2pdf` or `docxide`.");
#[cfg(not(any(feature = "office2pdf", feature = "docxide")))]
compile_error!("No engine chosen: feature `office2pdf` or `docxide`.");

/// Version of the protocol between the sidecar and the engine.
const PROTOCOL: u32 = 1;
/// The extraction does not accept a larger DOCX either.
const MAX_INPUT_BYTES: u64 = 64 * 1024 * 1024;

#[cfg(feature = "office2pdf")]
const ENGINE: (&str, &str) = ("office2pdf", "0.10.0");
#[cfg(feature = "docxide")]
const ENGINE: (&str, &str) = ("docxide-pdf", "0.20.1");

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {
        Some("info") => {
            println!(
                "{{\"engine\":\"{}\",\"version\":\"{}\",\"protocol\":{}}}",
                ENGINE.0, ENGINE.1, PROTOCOL
            );
            ExitCode::SUCCESS
        }
        Some("docx-to-pdf") => {
            let mut fonts = Vec::new();
            while let Some(arg) = args.next() {
                match (arg.as_str(), args.next()) {
                    ("--fonts", Some(dir)) => fonts.push(PathBuf::from(dir)),
                    _ => return fail(2, "Aufruf: docx-to-pdf [--fonts DIR]..."),
                }
            }
            docx_to_pdf(&fonts)
        }
        _ => fail(2, "Aufruf: info | docx-to-pdf [--fonts DIR]..."),
    }
}

fn docx_to_pdf(fonts: &[PathBuf]) -> ExitCode {
    let mut input = Vec::new();
    if io::stdin()
        .take(MAX_INPUT_BYTES + 1)
        .read_to_end(&mut input)
        .is_err()
    {
        return fail(2, "Eingabe nicht lesbar.");
    }
    if input.len() as u64 > MAX_INPUT_BYTES {
        return fail(3, "Eingabe zu gross.");
    }
    let pdf = match convert(&input, fonts) {
        Ok(pdf) => pdf,
        Err(kind) => return fail(4, kind),
    };
    let mut stdout = io::stdout().lock();
    if stdout.write_all(&pdf).and_then(|()| stdout.flush()).is_err() {
        return fail(4, "Ausgabe nicht schreibbar.");
    }
    ExitCode::SUCCESS
}

#[cfg(feature = "office2pdf")]
fn convert(input: &[u8], fonts: &[PathBuf]) -> Result<Vec<u8>, &'static str> {
    let options = office2pdf::config::ConvertOptions {
        font_paths: fonts.to_vec(),
        ..Default::default()
    };
    office2pdf::convert_bytes(input, office2pdf::config::Format::Docx, &options)
        .map(|result| result.pdf)
        .map_err(|_| "Umwandlung gescheitert.")
}

#[cfg(feature = "docxide")]
fn convert(input: &[u8], fonts: &[PathBuf]) -> Result<Vec<u8>, &'static str> {
    // docxide reads additional font directories only from the environment.
    if !fonts.is_empty() {
        let joined = std::env::join_paths(fonts).map_err(|_| "Schriftordner ungueltig.")?;
        std::env::set_var("DOCXIDE_FONTS", joined);
    }
    docxide_pdf::convert_docx_bytes_to_pdf_bytes(input).map_err(|_| "Umwandlung gescheitert.")
}

fn fail(code: u8, message: &str) -> ExitCode {
    eprintln!("{message}");
    ExitCode::from(code)
}
