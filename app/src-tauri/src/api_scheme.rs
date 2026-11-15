//! Scheme `unname-api` for the UI.
//!
//! The WebView no longer calls the sidecar directly via `http://127.0.0.1` but
//! via `unname-api://localhost/…` (on Windows `http://unname-api.localhost/…`).
//! The shell forwards the method, path, query and body to the sidecar and sets
//! the token itself. The WebView knows neither the token nor the port or
//! socket; a script in the UI only achieves what the UI is already allowed to
//! do.
//!
//! Only a few headers are forwarded in both directions. An `Authorization`
//! header from the WebView is dropped, as are cookies.

use std::borrow::Cow;

use serde_json::json;
use tauri::http::{header, HeaderName, HeaderValue, Method, Request, Response, StatusCode};
use tauri::{AppHandle, Manager, Runtime, UriSchemeContext, UriSchemeResponder};

use crate::saving::EntryOrigins;
use crate::sidecar::{describe_request_error, SidecarConnection, SidecarState};

pub const SCHEME: &str = "unname-api";

/// Base address under which the WebView reaches the scheme. WebView2 and
/// Android map their own schemes to `http://<schema>.localhost`.
pub fn webview_base_url() -> &'static str {
    if cfg!(any(windows, target_os = "android")) {
        "http://unname-api.localhost"
    } else {
        "unname-api://localhost"
    }
}

const REQUEST_HEADERS: [HeaderName; 2] = [header::CONTENT_TYPE, header::ACCEPT];
const RESPONSE_HEADERS: [HeaderName; 3] = [
    header::CONTENT_TYPE,
    header::CONTENT_DISPOSITION,
    header::CACHE_CONTROL,
];

pub fn handle<R: Runtime>(
    context: UriSchemeContext<'_, R>,
    request: Request<Vec<u8>>,
    responder: UriSchemeResponder,
) {
    let app = context.app_handle().clone();
    tauri::async_runtime::spawn(async move {
        responder.respond(forward(&app, request).await);
    });
}

async fn forward<R: Runtime>(
    app: &AppHandle<R>,
    request: Request<Vec<u8>>,
) -> Response<Cow<'static, [u8]>> {
    // Same release as the former `sidecar_connection`: only while the sidecar
    // is ready. Upload and export are checked by the sidecar or the shell on
    // their own paths.
    let connection = app.state::<SidecarState>().connection();
    let deletion = deleted_entries(request.method(), &request);
    let response = proxy(connection, request).await;
    // If the sidecar deletes an entry, the shell also forgets its original
    // paths and target folders.
    if let (Some(deletion), true) = (deletion, response.status() == StatusCode::NO_CONTENT) {
        if let Some(origins) = app.try_state::<EntryOrigins>() {
            match deletion {
                Deletion::One(entry_id) => origins.forget(&entry_id),
                Deletion::All => origins.forget_all(),
            }
        }
    }
    response
}

/// Determines which entries are deleted by a request: `DELETE /entries/{id}` or
/// „Verlauf leeren" (`DELETE /entries?confirm=true`).
#[derive(Debug, PartialEq)]
enum Deletion {
    One(String),
    All,
}

fn deleted_entries<T>(method: &Method, request: &Request<T>) -> Option<Deletion> {
    if method != Method::DELETE {
        return None;
    }
    let path = request.uri().path();
    let rest = path.strip_prefix("/entries")?;
    if rest.is_empty() || rest == "/" {
        return Some(Deletion::All);
    }
    let id = rest.strip_prefix('/')?;
    (!id.is_empty() && !id.contains('/')).then(|| Deletion::One(id.to_string()))
}

async fn proxy(
    connection: Option<SidecarConnection>,
    request: Request<Vec<u8>>,
) -> Response<Cow<'static, [u8]>> {
    let origin = request.headers().get(header::ORIGIN).cloned();
    if request.method() == Method::OPTIONS {
        return with_cors(Response::builder().status(StatusCode::NO_CONTENT), origin)
            .body(Cow::Borrowed(&[][..]))
            .expect("Antwort auf Preflight");
    }
    let Some(path) = sidecar_path(&request) else {
        return failure(StatusCode::BAD_REQUEST, "Ungültiger Pfad.", origin);
    };
    let Some(connection) = connection else {
        return failure(
            StatusCode::SERVICE_UNAVAILABLE,
            "Die lokale Verarbeitung ist nicht bereit.",
            origin,
        );
    };
    let Ok(method) = reqwest::Method::from_bytes(request.method().as_str().as_bytes()) else {
        return failure(
            StatusCode::METHOD_NOT_ALLOWED,
            "Unbekannte Methode.",
            origin,
        );
    };

    let mut outgoing = connection
        .client()
        .request(method, format!("{}{path}", connection.base_url))
        .bearer_auth(&connection.token);
    for name in REQUEST_HEADERS {
        if let Some(value) = request.headers().get(&name) {
            outgoing = outgoing.header(name.as_str(), value.as_bytes());
        }
    }
    let response = match outgoing.body(request.into_body()).send().await {
        Ok(response) => response,
        Err(error) => {
            log::warn!(
                "Aufruf über {SCHEME} nicht zustellbar: {}",
                describe_request_error(&error)
            );
            return failure(
                StatusCode::BAD_GATEWAY,
                "Die lokale Verarbeitung antwortet gerade nicht.",
                origin,
            );
        }
    };

    let status = response.status().as_u16();
    let mut builder = Response::builder().status(status);
    for name in RESPONSE_HEADERS {
        if let Some(value) = response.headers().get(name.as_str()) {
            if let Ok(value) = HeaderValue::from_bytes(value.as_bytes()) {
                builder = builder.header(name, value);
            }
        }
    }
    let body = match response.bytes().await {
        Ok(body) => body,
        Err(error) => {
            log::warn!(
                "Antwort über {SCHEME} abgebrochen: {}",
                describe_request_error(&error)
            );
            return failure(
                StatusCode::BAD_GATEWAY,
                "Die lokale Verarbeitung antwortet gerade nicht.",
                origin,
            );
        }
    };
    with_cors(builder, origin)
        .body(Cow::Owned(body.to_vec()))
        .unwrap_or_else(|_| {
            failure(
                StatusCode::BAD_GATEWAY,
                "Antwort der lokalen Verarbeitung unlesbar.",
                None,
            )
        })
}

/// Path including query, as the sidecar receives it. Only an absolute path
/// without authority (`//host`) passes through: the target is always the
/// sidecar.
fn sidecar_path<T>(request: &Request<T>) -> Option<String> {
    let path = request
        .uri()
        .path_and_query()
        .map(|value| value.as_str())
        .unwrap_or("/");
    (path.starts_with('/') && !path.starts_with("//")).then(|| path.to_string())
}

/// WebKit treats the scheme as a foreign origin; without this header it
/// discards the response. The response goes back only to the requesting origin,
/// which is always the UI itself.
fn with_cors(
    builder: tauri::http::response::Builder,
    origin: Option<HeaderValue>,
) -> tauri::http::response::Builder {
    builder
        .header(
            header::ACCESS_CONTROL_ALLOW_ORIGIN,
            origin.unwrap_or_else(|| HeaderValue::from_static("*")),
        )
        .header(header::VARY, "Origin")
        .header(
            header::ACCESS_CONTROL_ALLOW_METHODS,
            "GET, POST, PUT, PATCH, DELETE",
        )
        .header(header::ACCESS_CONTROL_ALLOW_HEADERS, "content-type, accept")
        .header(
            header::ACCESS_CONTROL_EXPOSE_HEADERS,
            "content-type, content-disposition",
        )
}

/// Return the rejection in the same format as the sidecar (`detail`) so the UI
/// can read it like any other response.
fn failure(
    status: StatusCode,
    detail: &str,
    origin: Option<HeaderValue>,
) -> Response<Cow<'static, [u8]>> {
    let body = json!({ "detail": detail }).to_string().into_bytes();
    with_cors(Response::builder().status(status), origin)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Cow::Owned(body))
        .expect("Fehlerantwort")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(uri: &str) -> Request<Vec<u8>> {
        Request::builder().uri(uri).body(Vec::new()).unwrap()
    }

    #[test]
    fn path_and_query_pass_through_unchanged() {
        assert_eq!(
            sidecar_path(&request("unname-api://localhost/entries/abc/files?seite=2")).as_deref(),
            Some("/entries/abc/files?seite=2")
        );
        assert_eq!(
            sidecar_path(&request("http://unname-api.localhost/health")).as_deref(),
            Some("/health")
        );
        assert_eq!(
            sidecar_path(&request("unname-api://localhost")).as_deref(),
            Some("/")
        );
    }

    #[test]
    fn only_deleting_entries_forgets_origin() {
        let delete = |uri: &str| {
            let mut incoming = request(uri);
            *incoming.method_mut() = Method::DELETE;
            deleted_entries(&Method::DELETE, &incoming)
        };
        assert_eq!(
            delete("unname-api://localhost/entries/abc"),
            Some(Deletion::One("abc".to_string()))
        );
        assert_eq!(
            delete("unname-api://localhost/entries?confirm=true"),
            Some(Deletion::All)
        );
        assert_eq!(delete("unname-api://localhost/entries/abc/files/x"), None);
        assert_eq!(delete("unname-api://localhost/jobs/abc"), None);
        assert_eq!(delete("unname-api://localhost/entriesx"), None);
        assert_eq!(
            deleted_entries(&Method::GET, &request("unname-api://localhost/entries/abc")),
            None
        );
    }

    #[test]
    fn no_detour_through_foreign_authority() {
        let mut foreign = request("unname-api://localhost/x");
        *foreign.uri_mut() = tauri::http::Uri::from_static("//evil.example/x");
        assert_eq!(sidecar_path(&foreign), None);
    }

    #[test]
    fn errors_use_sidecar_format() {
        let response = failure(
            StatusCode::SERVICE_UNAVAILABLE,
            "nicht bereit",
            Some(HeaderValue::from_static("tauri://localhost")),
        );
        assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(
            response.headers()[header::ACCESS_CONTROL_ALLOW_ORIGIN],
            "tauri://localhost"
        );
        let body: serde_json::Value = serde_json::from_slice(response.body()).unwrap();
        assert_eq!(body, json!({"detail": "nicht bereit"}));
    }

    #[test]
    fn nothing_forwarded_without_ready_sidecar() {
        let response =
            tauri::async_runtime::block_on(proxy(None, request("unname-api://localhost/health")));
        assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    }

    #[test]
    fn shell_answers_preflight_itself() {
        let mut preflight = request("unname-api://localhost/entries");
        *preflight.method_mut() = Method::OPTIONS;
        let response = tauri::async_runtime::block_on(proxy(None, preflight));
        assert_eq!(response.status(), StatusCode::NO_CONTENT);
        assert!(response
            .headers()
            .contains_key(header::ACCESS_CONTROL_ALLOW_METHODS));
    }

    #[cfg(unix)]
    #[test]
    fn forwarding_sets_token_and_drops_foreign_headers() {
        use std::io::{Read, Write};
        use std::os::unix::net::UnixListener;

        let directory = tempfile::Builder::new()
            .prefix("unname-")
            .tempdir()
            .unwrap();
        let path = directory.path().join("s");
        let listener = UnixListener::bind(&path).unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut buffer = [0u8; 4096];
            loop {
                let read = stream.read(&mut buffer).unwrap();
                request.extend_from_slice(&buffer[..read]);
                let text = String::from_utf8_lossy(&request);
                if let Some(end) = text.find("\r\n\r\n") {
                    if request.len() >= end + 4 + 9 {
                        break;
                    }
                }
            }
            stream
                .write_all(
                    b"HTTP/1.1 201 Created\r\ncontent-type: application/json\r\n\
                      x-intern: geheim\r\nwww-authenticate: Bearer\r\n\
                      content-length: 11\r\nconnection: close\r\n\r\n{\"ok\":true}",
                )
                .unwrap();
            String::from_utf8(request).unwrap()
        });

        let connection = SidecarConnection::unix(path, "sitzungstoken".to_string()).unwrap();
        let incoming = Request::builder()
            .method(Method::POST)
            .uri("unname-api://localhost/entries?x=1")
            .header(header::ORIGIN, "tauri://localhost")
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::AUTHORIZATION, "Bearer aus-der-webview")
            .header(header::COOKIE, "a=b")
            .body(br#"{"a":1234}"#.to_vec())
            .unwrap();
        let response = tauri::async_runtime::block_on(proxy(Some(connection), incoming));

        assert_eq!(response.status(), StatusCode::CREATED);
        assert_eq!(response.body().as_ref(), br#"{"ok":true}"#);
        assert_eq!(response.headers()[header::CONTENT_TYPE], "application/json");
        assert_eq!(
            response.headers()[header::ACCESS_CONTROL_ALLOW_ORIGIN],
            "tauri://localhost"
        );
        assert!(!response.headers().contains_key("x-intern"));
        assert!(!response.headers().contains_key(header::WWW_AUTHENTICATE));

        let seen = server.join().unwrap();
        let lower = seen.to_lowercase();
        assert!(lower.starts_with("post /entries?x=1 http/1.1"), "{seen}");
        assert!(lower.contains("authorization: bearer sitzungstoken"));
        assert!(!lower.contains("aus-der-webview"));
        assert!(!lower.contains("cookie"));
        assert!(lower.contains("content-type: application/json"));
        assert!(seen.ends_with(r#"{"a":1234}"#));
    }

    #[test]
    fn base_url_per_platform() {
        let base = webview_base_url();
        if cfg!(windows) {
            assert_eq!(base, "http://unname-api.localhost");
        } else {
            assert_eq!(base, "unname-api://localhost");
        }
    }
}
