//! The shell connects to the `org.unname.desktop.download` sidecar. The sidecar
//! resides in `Contents/XPCServices`, has its own sandbox, and is the only part
//! of the app with outbound network access for model files. TLS and HTTP to
//! external servers thus run outside the shell, which holds the user's files
//! and shares. Per file: the shell creates a pipe (socket pair) and sends the
//! service the address, allowed hosts, offset, and the write end of the pipe.
//! The service writes a header (`{"status","content_length"}`) and then the
//! bytes; it then closes the pipe and replies to the message with the result.
//! The shell reads piece by piece as before, checks size and SHA-256, and
//! writes the file (`models.rs`). If the shell closes its end (abort), the
//! service aborts.

use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::io::{BufRead, BufReader, Read};
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::path::PathBuf;

use serde::Deserialize;
use tokio::sync::{mpsc, oneshot};

use crate::models::{failure, ModelFailure};

pub const SERVICE_NAME: &str = "org.unname.desktop.download";
const PROTOCOL: i64 = 1;
const CHUNK: usize = 64 * 1024;
/// The header is short; anything more indicates a broken service.
const HEADER_MAX: u64 = 1024;

type XpcObject = *mut c_void;

#[allow(non_upper_case_globals)]
extern "C" {
    static _xpc_type_dictionary: c_void;
    fn xpc_connection_create(name: *const c_char, target_queue: *mut c_void) -> XpcObject;
    fn xpc_connection_set_event_handler(
        connection: XpcObject,
        handler: &block2::Block<dyn Fn(XpcObject)>,
    );
    fn xpc_connection_resume(connection: XpcObject);
    fn xpc_connection_cancel(connection: XpcObject);
    fn xpc_connection_send_message_with_reply_sync(
        connection: XpcObject,
        message: XpcObject,
    ) -> XpcObject;
    fn xpc_dictionary_create(
        keys: *const *const c_char,
        values: *const XpcObject,
        count: usize,
    ) -> XpcObject;
    fn xpc_dictionary_set_string(dictionary: XpcObject, key: *const c_char, value: *const c_char);
    fn xpc_dictionary_set_int64(dictionary: XpcObject, key: *const c_char, value: i64);
    fn xpc_dictionary_set_fd(dictionary: XpcObject, key: *const c_char, fd: c_int);
    fn xpc_dictionary_set_value(dictionary: XpcObject, key: *const c_char, value: XpcObject);
    fn xpc_dictionary_get_string(dictionary: XpcObject, key: *const c_char) -> *const c_char;
    fn xpc_dictionary_get_int64(dictionary: XpcObject, key: *const c_char) -> i64;
    fn xpc_dictionary_get_bool(dictionary: XpcObject, key: *const c_char) -> bool;
    fn xpc_array_create(objects: *const XpcObject, count: usize) -> XpcObject;
    fn xpc_array_set_string(array: XpcObject, index: usize, value: *const c_char);
    fn xpc_get_type(object: XpcObject) -> *const c_void;
    fn xpc_release(object: XpcObject);
}

/// `XPC_ARRAY_APPEND`
const ARRAY_APPEND: usize = usize::MAX;

/// An `libxpc` object with its own reference; it releases it upon leaving.
struct Owned(XpcObject);

// libxpc objects are thread-safe reference-counted.
unsafe impl Send for Owned {}

impl Drop for Owned {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { xpc_release(self.0) };
        }
    }
}

fn key(name: &str) -> CString {
    CString::new(name).expect("Schlüssel ohne Nullbyte")
}

/// Is the service in the bundle of the running app? Only then can `libxpc` find
/// it: it searches in `<App>.app/Contents/XPCServices` of the main program.
pub fn bundled() -> bool {
    service_path().is_some_and(|path| path.is_dir())
}

fn service_path() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    // …/unname.app/Contents/MacOS/unname → …/unname.app/Contents
    let contents = exe.parent()?.parent()?;
    Some(
        contents
            .join("XPCServices")
            .join(format!("{SERVICE_NAME}.xpc")),
    )
}

#[derive(Debug, Deserialize)]
struct Header {
    status: u16,
    content_length: Option<u64>,
}

/// The service's response, as the shell reads it continuously.
pub struct Response {
    pub status: u16,
    #[allow(dead_code)]
    pub content_length: Option<u64>,
    chunks: mpsc::Receiver<std::io::Result<Vec<u8>>>,
    outcome: Option<oneshot::Receiver<Result<(), ModelFailure>>>,
}

impl Response {
    /// Next chunk; `None` at the end. The service's result is only counted at
    /// the end: if it aborted, this becomes an error.
    pub async fn chunk(&mut self) -> Result<Option<Vec<u8>>, ModelFailure> {
        match self.chunks.recv().await {
            Some(Ok(chunk)) => Ok(Some(chunk)),
            Some(Err(error)) => Err(failure("network", error.to_string())),
            None => {
                let outcome = match self.outcome.take() {
                    Some(outcome) => outcome.await.unwrap_or_else(|_| Err(unreachable_service())),
                    None => Ok(()),
                };
                outcome.map(|()| None)
            }
        }
    }
}

fn unreachable_service() -> ModelFailure {
    failure("network", "Der Download-Dienst antwortet nicht.")
}

/// Requests `url` from the service; with `offset > 0` from that byte.
pub async fn get(
    url: &str,
    hosts: &[String],
    offset: u64,
    user_agent: &str,
) -> Result<Response, ModelFailure> {
    let (reader, writer) = UnixStream::pair().map_err(|error| failure("io", error.to_string()))?;
    let message = request(url, hosts, offset, user_agent, writer.as_raw_fd())?;
    // `libxpc` has duplicated the descriptor. Without its own write end, the
    // reader sees the end as soon as the service closes its own.
    drop(writer);

    let (outcome_tx, outcome_rx) = oneshot::channel();
    std::thread::Builder::new()
        .name("download-xpc".into())
        .spawn(move || {
            let _ = outcome_tx.send(send(message));
        })
        .map_err(|error| failure("io", error.to_string()))?;

    let (header_tx, header_rx) = oneshot::channel();
    let (chunk_tx, chunk_rx) = mpsc::channel(4);
    std::thread::Builder::new()
        .name("download-pipe".into())
        .spawn(move || read_pipe(reader, header_tx, chunk_tx))
        .map_err(|error| failure("io", error.to_string()))?;

    match header_rx.await {
        Ok(Some(header)) => Ok(Response {
            status: header.status,
            content_length: header.content_length,
            chunks: chunk_rx,
            outcome: Some(outcome_rx),
        }),
        // No header: the service rejected the request or could not reach the
        // server. Its answer states the reason.
        _ => Err(outcome_rx
            .await
            .unwrap_or_else(|_| Err(unreachable_service()))
            .err()
            .unwrap_or_else(|| failure("network", "Der Download-Dienst lieferte nichts."))),
    }
}

fn request(
    url: &str,
    hosts: &[String],
    offset: u64,
    user_agent: &str,
    fd: c_int,
) -> Result<Owned, ModelFailure> {
    let text = |value: &str| {
        CString::new(value).map_err(|_| failure("host_not_allowed", "Ungültige Adresse."))
    };
    let url = text(url)?;
    let user_agent = text(user_agent)?;
    let hosts = hosts
        .iter()
        .map(|host| text(host))
        .collect::<Result<Vec<_>, _>>()?;
    let offset = i64::try_from(offset).map_err(|_| failure("io", "Versatz zu groß."))?;
    unsafe {
        let message = Owned(xpc_dictionary_create(std::ptr::null(), std::ptr::null(), 0));
        xpc_dictionary_set_int64(message.0, key("protocol").as_ptr(), PROTOCOL);
        xpc_dictionary_set_string(message.0, key("url").as_ptr(), url.as_ptr());
        xpc_dictionary_set_int64(message.0, key("offset").as_ptr(), offset);
        xpc_dictionary_set_string(message.0, key("user_agent").as_ptr(), user_agent.as_ptr());
        let array = Owned(xpc_array_create(std::ptr::null(), 0));
        for host in &hosts {
            xpc_array_set_string(array.0, ARRAY_APPEND, host.as_ptr());
        }
        xpc_dictionary_set_value(message.0, key("hosts").as_ptr(), array.0);
        xpc_dictionary_set_fd(message.0, key("fd").as_ptr(), fd);
        Ok(message)
    }
}

/// Sends the request and waits for the answer; it only arrives when the service
/// is finished or aborted. Therefore, it runs in its own thread.
fn send(message: Owned) -> Result<(), ModelFailure> {
    let name = key(SERVICE_NAME);
    unsafe {
        let connection = Owned(xpc_connection_create(name.as_ptr(), std::ptr::null_mut()));
        if connection.0.is_null() {
            return Err(unreachable_service());
        }
        // Required before `resume`. Connection errors arrive here additionally
        // as a response to the message and are evaluated there.
        let handler = block2::RcBlock::new(|_event: XpcObject| {});
        xpc_connection_set_event_handler(connection.0, &handler);
        xpc_connection_resume(connection.0);
        let reply = Owned(xpc_connection_send_message_with_reply_sync(
            connection.0,
            message.0,
        ));
        xpc_connection_cancel(connection.0);
        outcome(&reply)
    }
}

unsafe fn outcome(reply: &Owned) -> Result<(), ModelFailure> {
    if reply.0.is_null()
        || !std::ptr::eq(
            xpc_get_type(reply.0),
            std::ptr::addr_of!(_xpc_type_dictionary),
        )
    {
        // XPC_TYPE_ERROR: The service is missing, crashed, or terminated.
        return Err(unreachable_service());
    }
    if xpc_dictionary_get_bool(reply.0, key("ok").as_ptr()) {
        return Ok(());
    }
    let read = |name: &str| {
        let value = xpc_dictionary_get_string(reply.0, key(name).as_ptr());
        (!value.is_null()).then(|| CStr::from_ptr(value).to_string_lossy().into_owned())
    };
    let code = match read("code").as_deref() {
        Some("host_not_allowed") => "host_not_allowed",
        Some("cancelled") => "cancelled",
        _ => "network",
    };
    let mut message = read("message").unwrap_or_else(|| "Download gescheitert.".to_string());
    let status = xpc_dictionary_get_int64(reply.0, key("http_status").as_ptr());
    if status > 0 && code == "network" {
        message.push_str(&format!(" (HTTP {status})"));
    }
    Err(failure(code, message))
}

/// Reads the header and bytes from the pipe. Stops when the sidecar closes the
/// pipe or the shell stops listening; then the reading end closes and the
/// sidecar aborts.
fn read_pipe(
    reader: UnixStream,
    header_tx: oneshot::Sender<Option<Header>>,
    chunk_tx: mpsc::Sender<std::io::Result<Vec<u8>>>,
) {
    let mut reader = BufReader::with_capacity(CHUNK, reader);
    let mut line = Vec::new();
    let header = match (&mut reader).take(HEADER_MAX).read_until(b'\n', &mut line) {
        Ok(_) if line.ends_with(b"\n") => serde_json::from_slice::<Header>(&line).ok(),
        _ => None,
    };
    let ok = header.is_some();
    if header_tx.send(header).is_err() || !ok {
        return;
    }
    loop {
        let mut chunk = vec![0u8; CHUNK];
        match reader.read(&mut chunk) {
            Ok(0) => return,
            Ok(read) => {
                chunk.truncate(read);
                if chunk_tx.blocking_send(Ok(chunk)).is_err() {
                    return;
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(error) => {
                let _ = chunk_tx.blocking_send(Err(error));
                return;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn header_and_bytes_from_pipe() {
        let (reader, mut writer) = UnixStream::pair().unwrap();
        let (header_tx, header_rx) = oneshot::channel();
        let (chunk_tx, mut chunk_rx) = mpsc::channel(4);
        let thread = std::thread::spawn(move || read_pipe(reader, header_tx, chunk_tx));
        writer
            .write_all(b"{\"content_length\":5,\"status\":206}\nhallo")
            .unwrap();
        drop(writer);

        let header = tauri::async_runtime::block_on(header_rx).unwrap().unwrap();
        assert_eq!(header.status, 206);
        assert_eq!(header.content_length, Some(5));
        let mut bytes = Vec::new();
        while let Some(chunk) = chunk_rx.blocking_recv() {
            bytes.extend(chunk.unwrap());
        }
        assert_eq!(bytes, b"hallo");
        thread.join().unwrap();
    }

    #[test]
    fn no_content_without_header() {
        let (reader, mut writer) = UnixStream::pair().unwrap();
        let (header_tx, header_rx) = oneshot::channel();
        let (chunk_tx, mut chunk_rx) = mpsc::channel(4);
        writer.write_all(b"kein json\n").unwrap();
        drop(writer);
        read_pipe(reader, header_tx, chunk_tx);
        assert!(tauri::async_runtime::block_on(header_rx).unwrap().is_none());
        assert!(chunk_rx.blocking_recv().is_none());
    }

    #[test]
    fn request_carries_all_fields() {
        let (_reader, writer) = UnixStream::pair().unwrap();
        let hosts = vec!["huggingface.co".to_string(), "*.hf.co".to_string()];
        let message = request(
            "https://huggingface.co/x",
            &hosts,
            7,
            "unname/0.1.0",
            writer.as_raw_fd(),
        )
        .unwrap();
        unsafe {
            let read = |name: &str| {
                CStr::from_ptr(xpc_dictionary_get_string(message.0, key(name).as_ptr()))
                    .to_str()
                    .unwrap()
                    .to_string()
            };
            assert_eq!(read("url"), "https://huggingface.co/x");
            assert_eq!(read("user_agent"), "unname/0.1.0");
            assert_eq!(
                xpc_dictionary_get_int64(message.0, key("offset").as_ptr()),
                7
            );
            assert_eq!(
                xpc_dictionary_get_int64(message.0, key("protocol").as_ptr()),
                PROTOCOL
            );
        }
        assert!(request("https://x\0y", &hosts, 0, "ua", writer.as_raw_fd()).is_err());
    }

    #[test]
    fn no_service_without_bundle() {
        // The test run is not inside an app bundle.
        assert!(!bundled());
    }

    /// Performs a real download via the sidecar. Runs only from an app bundle
    /// with an embedded sidecar and network access (see
    /// `scripts/download-xpc-e2e.sh`).
    #[test]
    #[ignore]
    fn end_to_end_through_service() {
        let url = std::env::var("UNNAME_XPC_E2E_URL").expect("UNNAME_XPC_E2E_URL");
        let hosts: Vec<String> = std::env::var("UNNAME_XPC_E2E_HOSTS")
            .expect("UNNAME_XPC_E2E_HOSTS")
            .split(',')
            .map(str::to_string)
            .collect();
        assert!(
            bundled(),
            "Testprogramm liegt nicht in einem Bundle mit Dienst"
        );
        tauri::async_runtime::block_on(async {
            let mut response = get(&url, &hosts, 0, "unname-e2e").await.unwrap();
            assert_eq!(response.status, 200);
            let mut total = 0usize;
            while let Some(chunk) = response.chunk().await.unwrap() {
                total += chunk.len();
            }
            assert!(total > 0);
            println!("E2E: {total} Byte über den Dienst");

            let mut ranged = get(&url, &hosts, 10, "unname-e2e").await.unwrap();
            assert_eq!(ranged.status, 206, "Range wird durchgereicht");
            while ranged.chunk().await.unwrap().is_some() {}

            let refused = get("https://example.com/", &hosts, 0, "unname-e2e").await;
            assert_eq!(refused.err().unwrap().code, "host_not_allowed");
        });
    }
}
