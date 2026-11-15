# unname-download

The app's download service on the Mac, an XPC service at
`unname.app/Contents/XPCServices/org.unname.desktop.download.xpc`. It downloads
model files over HTTPS and is the only part of the package with network rights
of its own. It sees no documents, no paths and no files of the app, and writes
nothing itself.

## Interface (protocol 1)

The shell (`app/src-tauri/src/download_service.rs`) sends one message per file
and waits for the reply:

| Field | Type | Content |
|---|---|---|
| `protocol` | int64 | `1` |
| `url` | string | Address from `models.lock.json` or from the own server |
| `hosts` | [string] | Allowed hosts of the source; `*.x` allows subdomains, not `x` |
| `offset` | int64 | `> 0` resumes with `Range: bytes=offset-` |
| `user_agent` | string | `unname/<version>` |
| `fd` | fd | Write end of a pipe (socket pair) |

The service writes one JSON line `{"status":206,"content_length":…}` into the
pipe, then the bytes of the response, then closes it. The reply to the message
comes after that: `{ok, code, message, http_status}` with `code` one of
`invalid_request`, `host_not_allowed`, `network`, `cancelled`. If the shell
closes its end, the service aborts.

The service checks: HTTPS only, allowed hosts only, no credentials in the
address, at most five redirects and each only to an allowed host; TLS 1.2 or
newer with the system roots; no cookies, no cache, `Accept-Encoding: identity`.
The shell checks size and SHA-256.

## Entitlements

`Entitlements.plist`: `app-sandbox` and `network.client`, nothing else – no
`inherit` (the service is not a child of the app; launchd starts it) and no
`network.server`. `scripts/build-download-xpc.mjs` checks this after signing.

## Build and test

```bash
swift test --package-path download-xpc                          # validation rules
node scripts/build-download-xpc.mjs --target aarch64-apple-darwin  # bundle into app/src-tauri/binaries/
scripts/download-xpc-e2e.sh                                     # real download through the service (network)
```

libxpc finds the service only in the bundle of the calling program. In
development outside a bundle (`npm run tauri:dev`), the shell therefore
downloads by itself; a release without the service does not download.
