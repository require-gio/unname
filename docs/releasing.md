# Releasing

How a version of unname gets from main to a download on GitHub, and how the
model files get their GitHub mirror.

## Three tag families

| Tag | What it is | Made by |
|---|---|---|
| `v0.2.0` | An app release: DMG, Windows installers, checksums | [`release.yml`](../.github/workflows/release.yml) |
| `models-v1` | The model files the app downloads (second source after Hugging Face) | [`models-mirror.yml`](../.github/workflows/models-mirror.yml) |
| `bundle-inputs-<run id>` | The portable Tesseract that goes into the Windows package | [`bundle-inputs.yml`](../.github/workflows/bundle-inputs.yml) |

Only `v<major>.<minor>.<patch>` counts as an app version. The other two
releases are never marked "latest". The model tag has its own number:
`models-v1` stays as long as an app version that lists it is in use, and a new
set of model files gets `models-v2`.

## Making a release

```sh
node scripts/release.mjs            # asks patch / minor / major
node scripts/release.mjs --dry-run  # shows the PR it would open, changes nothing
```

1. The script shows the last release, the three versions you can choose, and
   the commits that would go out. It then opens a pull request from `main`
   into `release` with the label `release:patch`, `release:minor` or
   `release:major` (or updates the open one). The first time, it offers to
   create the `release` branch at main's first commit.
2. On the PR, **Release PR › Release label and version** checks that the PR
   comes from main and has exactly one release label. Its summary shows the
   version merging would release. To change your mind, swap the label. You can
   also open the PR in the GitHub UI and add the label there.
3. Merge with **Create a merge commit**. Squash or rebase would leave `release`
   with commits main does not have, and the next release PR would show old
   changes again.
4. The merge starts **Release**. It reads the label from the merged PR, bumps
   the highest `v*` tag, builds and tests both packages, and only then tags the
   merge commit and creates a **draft** release. The draft holds the files,
   short install notes and the list of merged PRs.
5. Download the DMG and the installer from the draft, try them, then click
   **Publish release**.

The first release starts from 0.0.0: `minor` gives 0.1.0, `major` gives 1.0.0.
Versions are plain `major.minor.patch`, because the Windows MSI accepts nothing
else (major and minor up to 255, patch up to 65535).

### What the pipeline builds and tests

| Job | Runner | Does |
|---|---|---|
| Version | ubuntu | label → version; a re-run reuses the tag already on the commit |
| macOS arm64 | `macos-15` | Vision helper and download service tests, `build-distribution.mjs --adhoc`, mounts the DMG, checks that the bundled sidecar and the version are correct, starts the app (`smoke-packaged-app.mjs`) |
| Windows x64 | `windows-2025` (hosted) | Tesseract from `bundle-inputs-<run id>`, verified against the trust lock; `build-distribution.mjs --unsigned`; installs the setup.exe silently and starts the installed app; unpacks the MSI and checks its contents |
| Tag and draft release | ubuntu | `SHA256SUMS.txt`, annotated tag, draft release |

The unit, sidecar, Rust and recall tests run on the release PR itself
(`build-guard.yml`, `quality-gate.yml`), like on any PR.

**Dry run.** Actions › Release › **Run workflow** builds and tests on any branch
with the level you pick. It keeps the packages as workflow artifacts for 14
days and creates no tag and no release. Use it to try the pipeline, or a change
to it, before a real release.

**A failed release** creates no tag. Fix the cause on main and open a new
release PR; for a flaky step, use **Re-run failed jobs**. A re-run builds the
same version again, because the version job reuses a tag that already points
at the commit.

### The version in the files

The pipeline writes the version into the checkout before it builds
(`scripts/release/set-version.mjs`): `app/package.json` and its lock,
`tauri.conf.json`, `Cargo.toml` and `Cargo.lock`, `sidecar/pyproject.toml`,
`uv.lock` and `presidio_sidecar/__init__.py`. It does not commit that change.
The tag is the record of what was released, and main and `release` keep their
development version, so a bot never pushes to a protected branch. To rebuild a
release locally:

```sh
git checkout v0.2.0
node scripts/release/set-version.mjs 0.2.0
node scripts/build-distribution.mjs --adhoc   # macOS; --unsigned on Windows
```

### Signing

Release packages are not signed with a developer identity yet. The macOS app
is signed ad hoc: the sandbox is active, but Gatekeeper asks for **Open Anyway**
once. The Windows installers carry no Authenticode signature, so SmartScreen
asks once. The install notes in [`.github/release-notes.md`](../.github/release-notes.md)
explain both. Once the certificates exist, the signed and notarized path in
[`package-smoke.yml`](../.github/workflows/package-smoke.yml) (Run workflow with
`signed_release`) is what `release.yml` should switch to.

## One-time setup

1. **Windows build inputs.** Run **Bundle-Eingaben bauen (portables Tesseract)**
   once (Actions › Run workflow). Its summary shows the trust entries. Review
   them and copy at least `windows-x64-release` into
   [`sidecar/packaging/bundle-inputs.lock.json`](../sidecar/packaging/bundle-inputs.lock.json)
   (`macos-arm64-release` is already there). Then set the repository variable
   `BUNDLE_INPUTS_RUN_ID` (Settings › Secrets and variables › Actions ›
   Variables) to that run's ID. The run also publishes the Tesseract build as
   the release `bundle-inputs-<run id>`, which `release.yml` downloads; workflow
   artifacts would expire after 90 days.
2. **Model mirror.** Run **Model mirror on GitHub** once, see below.
3. **Protect `release`** (Settings › Rules › Rulesets) once the branch exists:
   require a pull request, require the status checks *Release label and
   version*, the *Build-Guard* jobs and the *Recall-Quality Gate* jobs, and
   allow only merge commits.

## Model files on GitHub

The app's manifest [`app/src-tauri/models.lock.json`](../app/src-tauri/models.lock.json)
lists two addresses per file: Hugging Face (`Giorgi-KI/unname-app`, tag `v1`)
and GitHub. Under GitHub, the spaCy wheels and tessdata point to their vendors;
the files only we provide (`ner`, `classifier`) point to the release
`models-v1` of this repository, as `<component>--<file>`.

**Model mirror on GitHub** (Actions › Run workflow) fills that release. It
downloads exactly the files the manifest expects there from Hugging Face,
checks size and SHA-256, creates the release if needed (never "latest"),
uploads what is missing, and then downloads every GitHub address back and
checks it again. Uploaded files are never replaced; if the final check flags
one, delete that asset in the release and run the workflow again.

To check every address of the manifest from your own machine (it downloads
about 1.6 GB):

```sh
python3 scripts/models/mirror.py check                       # both sources
python3 scripts/models/mirror.py check --source huggingface  # one source
```

**New model files** need a new manifest and new tags on both sides, while the
old ones stay so older app versions still load: set `SPIEGEL_REVISION = "v2"`
in [`scripts/models/modelle.py`](../scripts/models/modelle.py), rebuild the
manifest (`modelle.py manifest …`, or `adressen` if only the addresses
change), upload to Hugging Face and tag it `v2` there, then run the mirror
workflow. It creates `models-v2`.
