## Download

| System | File |
|---|---|
| macOS 14 or later, Apple Silicon | `unname-{{VERSION}}-macos-arm64.dmg` |
| Windows 10 or 11, x64 | `unname-{{VERSION}}-windows-x64-setup.exe` (the `.msi` is for managed installs) |

**First start on macOS.** This build is not notarized by Apple yet. Open the
DMG and drag unname to Applications. The first time you open it, macOS says it
could not verify that unname is free of malware: click **Done**, open
**System Settings › Privacy & Security**, scroll to the message about unname
and click **Open Anyway**. You only need to do this once. If macOS instead says
the app is damaged, run `xattr -dr com.apple.quarantine /Applications/unname.app`
in Terminal and open it again.

**First start on Windows.** The installer is not code-signed yet. If Microsoft
Defender SmartScreen says "Windows protected your PC", click **More info**,
then **Run anyway**.

**Models.** On first start unname downloads its detection models once (about
600 MB) and checks every file against the SHA-256 built into the app. After
that, nothing leaves the device.

Checksums of all files: `SHA256SUMS.txt`.
