# Roadmap

What is still open. Everything here needs an account, a device, real documents,
a signature or a decision, or is code work that has not been done yet. Finished
work is in the git history. The behavior this serves is in [spec.md](spec.md).

## Release blockers

Unsigned releases go through the release PR and `release.yml`
([releasing.md](releasing.md)). These items stand between that and a signed,
complete release.

| Item | What is needed |
|---|---|
| **Signing, macOS** | An Apple Developer ID and notarization credentials (`APPLE_SIGNING_IDENTITY` plus an Apple ID or App Store Connect key). A provisioning profile with a keychain access group for the keychain work below. Until then `release.yml` builds with `node scripts/build-distribution.mjs --adhoc`: an ad hoc signed package with the real sandbox, not notarized, which opens on other Macs only after "Open Anyway". |
| **Signing, Windows** | A code-signing certificate installed on the build machine, `WINDOWS_CERTIFICATE_SHA1` and an HTTPS `WINDOWS_TIMESTAMP_URL`. Until then `release.yml` builds `--unsigned` and SmartScreen asks once. |
| **Model hosting** | Hugging Face (`Giorgi-KI/unname-app`, tag `v1`) answers for every address in `app/src-tauri/models.lock.json`, model card and licenses included (status check 2026-11-21). Still to do: run "Model mirror on GitHub" (`.github/workflows/models-mirror.yml`) once to fill the GitHub release `models-v1`, then `python3 scripts/models/mirror.py check` for size, SHA-256 and redirect hosts of every address. Any later change needs a new manifest **and** new tags (`v2`, `models-v2`); `v1` stays so older app versions still load. |
| **Trust lock** | Run the "Build bundle inputs (portable Tesseract)" workflow (`.github/workflows/bundle-inputs.yml`) once by hand and enter the resulting entries into `sidecar/packaging/bundle-inputs.lock.json`; at least `windows-x64-release`, which `release.yml` needs. Then set the repository variable `BUNDLE_INPUTS_RUN_ID` to that run. Without the lock a package cannot be built for that target; `macos-arm64` and `macos-arm64-release` (both without models) are already entered. |
| **Self-hosted runner** | A Windows 11 x64 runner with exactly 2 logical cores and 8 GB RAM, labels `windows-11` and `minimum-hardware`, for `package-smoke.yml` and A-14. |
| **Version number** | Chosen with the label of the first release PR: there is no app tag yet, so `release:minor` gives 0.1.0 and `release:major` 1.0.0. The pipeline stamps it into the package name, the review report and the diagnostics report. |
| **CI after pushing** | `bundle-inputs`, `package-smoke`, `release`, `release-pr` and `models-mirror` have not run yet. Try `release.yml` with "Run workflow" (a dry run without tag or release) before the first release PR. |

## External acceptance (spec section 11)

The three criteria the repository cannot prove on its own. The exact procedure
for each is under `external_check` in
[`acceptance/spec-11.json`](../acceptance/spec-11.json).

| Criterion | What is to do |
|---|---|
| **A-12** network capture | Start a signed package with an empty network cache and capture all non-loopback interfaces before starting (`sudo tcpdump -i any -n "not host 127.0.0.1 and not host ::1" -w run.pcapng`). Run 1: drop documents, review, save, restore an AI answer, open settings, quit. Run 2: also download a language pack. Then analyze which processes talk to which targets and file the result under `acceptance/`. |
| **A-14** limit device | Run `package-smoke.yml` on the runner above, then repeat the reference flow by hand (first-run setup with a real download, review a PDF, a scan and a text file, save, restore) and run `uv run --project sidecar python scripts/performance.py --output acceptance/leistung-grenzgeraet.json` there. |
| **A-22** real corpus | At least 200 real documents per area of use, pseudonymized beforehand, imported with `scripts/quality/import_real.py` (procedure in [testdata/quality/README.md](../testdata/quality/README.md)), then measured locally with `scripts/quality/gate.py` and `scripts/second_opinion/measure.py`. Only the content-free report goes into the repository. The documents stay on the operator's device. |

## Checks only a person can do in the running app

The smoke tests imitate the shell and the system dialogs. What only a human can
operate goes here, once on macOS and once on Windows.

In the signed or installed package (the draft release of `release.yml` has
the unsigned packages):

- **Package smoke with signature**, then a first-run setup with a real download
  through the download service. `lsof -nP -p <sidecar pid> -iTCP` must show no
  listener, `lsof -U` must show the socket, and Settings › Technical must say
  "without port".
- **A scan under the sandbox**: recognize and save it (A-29); check
  `codesign -d --entitlements` on the Vision helper (sandbox plus inheritance);
  the first start after installation shows "Preparing text recognition".
- **Package size and cold start** of the sidecar until the first hit in the
  installed package.
- **Dock and system integration**: a document dropped on the Dock icon,
  "Open with", cold start through a document; the Dock progress bar and the
  notification when the window is covered.
- **docx-preview in the real WebViews** (WKWebView and WebView2): the preview
  iframe cannot be checked in Playwright.

In the running app:

- **Clipboard**: Universal Clipboard to an iPhone, a clipboard manager (Maccy),
  the clipboard is empty after 120 s.
- **Energy**: `pmset -g assertions` and App Nap during a 50-page PDF.
- **Spotlight**: `mdfind` does not find the job temp folder.
- **Menus and dialogs**: ⌘, and ⌘O, the menu after switching to English, the
  open and save dialogs in German.
- **Finder**: the smart folder "Tag is protected".
- **Screen capture**: screenshots, QuickTime, Zoom/Teams.
- **iCloud and cloud folders**: a file with "Remove download", saving to iCloud
  Drive, OneDrive and Dropbox.
- **Windows**: `tauri build`; drop onto the window, the file and folder dialogs,
  "Paste from clipboard"; Alt+N and Alt+M in the real WebView.
- **Saving**: next to the originals under the macOS sandbox, the fallback to the
  folder dialog, "Open folder", "Edit again" after a restart.
- **Restoring**: drop an AI file, "Open" in the responsible program, "Save as …".
- **Settings**: export and import a backup, add and refresh a name list, export
  and import a template.
- **Models**: first-run setup with a real download, a model package from a file,
  check and remove, load a language pack, load the second opinion.
- **Fail-closed**: `kill -9` the sidecar while an entry is open — the message,
  the lock, and the readable document.
- **Fresh start**: `node scripts/reset-state.mjs --yes`, then check that the coach
  marks appear once by themselves.

Whatever these checks find should be fixed and covered by a test.

## Measurements on other devices

- **OCR gate on real hardware**: run
  `uv run --project sidecar python -m scripts.quality.ocr_gate run --engine apple-vision --output ocr-<device>.json`
  (after `npm ci --prefix app` for the fonts) on an M1 with 8 GB and a current M
  device. Only the M1 Max is measured so far
  ([`acceptance/ocr-mac.json`](../acceptance/ocr-mac.json)). Also measure the
  first-call time (compilation) on an Intel Mac and the M1, and two parallel
  calls. Then update the baseline (`--update-baseline`, one key per macOS major
  version).
- **After every macOS major release**: run the OCR gate again and repeat the
  probe that checks whether WKWebView under the sandbox loads without
  `network.client`; only if it does is dropping that entitlement worthwhile.
- **`scripts/performance.py` on two devices** before the worker matrix is
  aligned to performance cores.
- **CI VM baseline**: adopt the `ci-vm` baseline from the first CI run.

## Decisions

| Question | Recommendation |
|---|---|
| How long may restored text stay on the clipboard, and should that limit be switchable? (today 120 s) | 120 s, switchable in the settings |
| Remove the GPU detection (`hardware.py`, `PRESIDIO_DEVICE`, the device line)? PyTorch is gone from the sidecar, so nothing uses a GPU. | Remove it as soon as no other model needs a GPU |
| TLS roots of the download service: the system's (today) or built-in ones like the shell used? | Keep the system's, so corporate proxies with their own CA work; the checksums in `models.lock.json` secure the content anyway |
| Run the sidecar without TCP on Windows too (named pipe or AF_UNIX)? | Defer until the Windows text recognition has been assessed; the token is already out of the WebView there |
| Touch ID before "Restore" and "Export backup"? | Optional, off by default |
| Hide the window in screen sharing? | A setting, off by default |
| Put the secret in the OS keychain (macOS Keychain, Windows Credential Manager) with the file only as a fallback? | Yes. Needs the signed package to verify, so it waits for the signing items above |

## Code work

- **Keychain**: the secret of the translation table in the OS keychain, with a
  migration of the existing file.
- **Faster saving of scans**, and a **hint at the first scan** that text
  recognition is being prepared once.
- **Folder onto the Dock icon**, and **progress for the whole app** in the Dock.
- **Models package on ONNX**: switch the package with models (`--with-models`,
  `package-smoke.yml`, `bundle-inputs.yml`) from the PyTorch snapshot to the ONNX
  folder, checked against `models.lock.json`; and run the recall gate with the
  real model in CI.
- **More countries**: CH, UK, US, with recognizers, negative tests, field names
  and phone regions.
- **Cleanup without behavior change**: remove the remaining profile helpers of
  the old UI (`Profile.apply_defaults`, `assert_export_allowed`, `validate_mode`)
  with their tests, and move the Starlette constants
  `HTTP_413_REQUEST_ENTITY_TOO_LARGE` / `HTTP_422_UNPROCESSABLE_ENTITY` to their
  new names.
- **Compare the reference images with the mockup**, note deviations, implement
  them and regenerate the images with `npm run test:smoke:update`. The images
  under `app/tests/smoke/references/` are the reference Playwright compares
  against.

## Housekeeping on the dev machine

- The container of the sandbox probe (`~/Library/Containers/org.unname.spike.z0`)
  is protected by macOS and could not be deleted by a script; remove it in the
  Finder.
- The old debug bundle `app/src-tauri/target/debug/bundle/macos/unname.app`
  still holds a placeholder sidecar from a test; the next bundle build replaces
  it.
