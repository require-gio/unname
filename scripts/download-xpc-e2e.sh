#!/usr/bin/env bash
# End‑to‑end test of the download service, macOS only.
#
# libxpc only discovers a service inside the caller's bundle. Therefore this
# test places the Rust test binary in a temporary bundle next to the
# (ad‑hoc, sandbox‑signed, network.client‑signed) service and runs the
# otherwise skipped `end_to_end_through_service` test: real download,
# resume via Range, and rejection of a foreign host.
#
# Usage: scripts/download-xpc-e2e.sh   (requires network)
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
target="$(uname -m | sed 's/arm64/aarch64/')-apple-darwin"

node "$root/scripts/build-download-xpc.mjs" --target "$target"
binary="$(cd "$root/app/src-tauri" && cargo test --lib --no-run --message-format=json 2>/dev/null \
  | python3 -c 'import json,sys
for line in sys.stdin:
    m = json.loads(line)
    if m.get("reason") == "compiler-artifact" and m.get("executable") and m["target"]["name"] == "app_lib":
        print(m["executable"])' | tail -1)"
test -x "$binary"

bundle="$(mktemp -d)/Probe.app"
mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/XPCServices"
cp "$binary" "$bundle/Contents/MacOS/probe"
cp -R "$root/app/src-tauri/binaries/org.unname.desktop.download.xpc" "$bundle/Contents/XPCServices/"
cat > "$bundle/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>probe</string>
<key>CFBundleIdentifier</key><string>org.unname.desktop.probe</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
PLIST
codesign --force --sign - "$bundle/Contents/MacOS/probe"

UNNAME_XPC_E2E_URL="${UNNAME_XPC_E2E_URL:-https://huggingface.co/Giorgi-KI/unname-app/resolve/v1/classifier/zweitmeinung.json}" \
UNNAME_XPC_E2E_HOSTS="${UNNAME_XPC_E2E_HOSTS:-huggingface.co,*.huggingface.co,*.hf.co}" \
  "$bundle/Contents/MacOS/probe" --ignored --exact download_service::tests::end_to_end_through_service --nocapture
rm -rf "$(dirname "$bundle")"
