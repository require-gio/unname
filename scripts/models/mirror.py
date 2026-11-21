#!/usr/bin/env python3
"""Fill and check the download addresses of ``app/src-tauri/models.lock.json``.

The app downloads each model file from Hugging Face or, as a second source, from
GitHub. On GitHub our own files live in a release of this repository whose tag
(``models-v1``) is kept apart from the app releases (``v0.2.0``); files the
vendor already hosts (spaCy wheels, tessdata) point to the vendor instead.

``stage`` downloads every file that the manifest expects in our GitHub release
from Hugging Face, checks size and SHA-256 against the manifest and writes it
under its release asset name. ``.github/workflows/models-mirror.yml`` uploads
that folder. ``check`` downloads every address in full and checks size, SHA-256
and that each redirect stays on the hosts the manifest allows for its source.

Usage (from the repository root, standard library only):

    python3 scripts/models/mirror.py stage --hf-revision main --output /tmp/mirror
    python3 scripts/models/mirror.py check [--source github]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "app" / "src-tauri" / "models.lock.json"
DEFAULT_REPOSITORY = "require-gio/unname"
USER_AGENT = "unname-model-mirror"
CHUNK = 1024 * 1024
ATTEMPTS = 3

_RELEASE_URL = re.compile(
    r"^https://github\.com/(?P<repository>[^/]+/[^/]+)/releases/download/"
    r"(?P<tag>[^/]+)/(?P<asset>[^/]+)$"
)
_HF_URL = re.compile(r"^(https://huggingface\.co/[^/]+/[^/]+/resolve/)[^/]+(/.+)$")


@dataclass(frozen=True)
class Download:
    """One file of the manifest at one address."""

    label: str
    url: str
    source: str
    size: int
    sha256: str


@dataclass(frozen=True)
class MirrorFile:
    """A file our own GitHub release has to carry."""

    asset: str
    download: Download


class DownloadError(Exception):
    pass


def host_allowed(host: str, patterns: list[str]) -> bool:
    """``*.hf.co`` allows any subdomain of hf.co, not hf.co itself."""

    host = host.lower().rstrip(".")
    for pattern in patterns:
        pattern = pattern.lower()
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]) and host != pattern[2:]:
                return True
        elif host == pattern:
            return True
    return False


def all_downloads(manifest: dict, source: str | None = None) -> list[Download]:
    downloads = []
    for component in manifest["components"]:
        for entry in component["files"]:
            for name, url in sorted(entry["urls"].items()):
                if source is None or name == source:
                    downloads.append(
                        Download(
                            label=f"{component['id']}/{entry['path']}",
                            url=url,
                            source=name,
                            size=entry["size"],
                            sha256=entry["sha256"],
                        )
                    )
    return downloads


def mirror_plan(
    manifest: dict, repository: str, hf_revision: str | None
) -> tuple[str, list[MirrorFile]]:
    """The release tag and the files it needs, each fetched from Hugging Face."""

    tags: set[str] = set()
    files: list[MirrorFile] = []
    for component in manifest["components"]:
        for entry in component["files"]:
            match = _RELEASE_URL.match(entry["urls"]["github"])
            if not match or match["repository"] != repository:
                continue  # hosted by the vendor, not by us
            tags.add(match["tag"])
            source = entry["urls"]["huggingface"]
            if hf_revision is not None:
                hf = _HF_URL.match(source)
                if not hf:
                    raise SystemExit(f"Not a Hugging Face resolve address: {source}")
                source = f"{hf[1]}{quote(hf_revision, safe='')}{hf[2]}"
            label = f"{component['id']}/{entry['path']}"
            download = Download(label, source, "huggingface", entry["size"], entry["sha256"])
            files.append(MirrorFile(match["asset"], download))
    if not files:
        raise SystemExit(f"The manifest has no addresses in a release of {repository}.")
    if len(tags) != 1:
        raise SystemExit(f"The manifest uses several release tags: {', '.join(sorted(tags))}.")
    assets = [file.asset for file in files]
    if len(set(assets)) != len(assets):
        raise SystemExit("Two manifest files map to the same release asset name.")
    (tag,) = tags
    if re.fullmatch(r"v\d+(\.\d+)*", tag):
        raise SystemExit(f"{tag} looks like an app version; model releases need their own prefix.")
    return tag, files


class _AllowedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts: list[str]) -> None:
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _require_allowed(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _require_allowed(url: str, hosts: list[str]) -> None:
    address = urlparse(url)
    if address.scheme != "https" or not host_allowed(address.hostname or "", hosts):
        raise DownloadError(f"address outside the allowed hosts {hosts}: {url}")


def fetch(download: Download, hosts: list[str], target: Path | None) -> None:
    """Download in full and compare with the manifest; write to ``target`` if given."""

    _require_allowed(download.url, hosts)
    opener = urllib.request.build_opener(_AllowedRedirects(hosts))
    request = urllib.request.Request(download.url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, ATTEMPTS + 1):
        partial = target.with_name(target.name + ".part") if target else None
        try:
            digest, size = hashlib.sha256(), 0
            with opener.open(request, timeout=60) as response:
                sink = partial.open("wb") if partial else None
                try:
                    while block := response.read(CHUNK):
                        digest.update(block)
                        size += len(block)
                        if sink:
                            sink.write(block)
                finally:
                    if sink:
                        sink.close()
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if partial:
                partial.unlink(missing_ok=True)
            if isinstance(error, urllib.error.HTTPError) and error.code < 500:
                raise DownloadError(f"HTTP {error.code} for {download.url}") from error
            if attempt == ATTEMPTS:
                raise DownloadError(f"{error} for {download.url}") from error
            time.sleep(5 * attempt)
    if size != download.size or digest.hexdigest() != download.sha256:
        if partial:
            partial.unlink(missing_ok=True)
        raise DownloadError(
            f"{download.label} from {download.url}: {size} bytes, SHA-256 {digest.hexdigest()}; "
            f"expected {download.size} bytes, {download.sha256}"
        )
    if target and partial:
        partial.replace(target)


def stage(
    manifest: dict, repository: str, hf_revision: str | None, output: Path, notes: Path | None
) -> str:
    tag, files = mirror_plan(manifest, repository, hf_revision)
    hosts = manifest["sources"]["huggingface"]["hosts"]
    output.mkdir(parents=True, exist_ok=True)
    for file in files:
        target = output / file.asset
        print(f"  … {file.asset} ← {file.download.url}", file=sys.stderr)
        fetch(file.download, hosts, target)
    if notes is not None:
        notes.write_text(release_notes(manifest, tag, files), encoding="utf-8")
    return tag


def release_notes(manifest: dict, tag: str, files: list[MirrorFile]) -> str:
    rows = "\n".join(
        f"| `{file.asset}` | {file.download.size} | `{file.download.sha256}` |" for file in files
    )
    return f"""Model files for unname, manifest revision {manifest["revision"]}.

These are not an app release: the app downloads them during first-run setup,
from Hugging Face or from here, and installs a file only if its size and
SHA-256 match the manifest built into the app. The tag `{tag}` stays as long as
app versions that list it are in use; a new set of files gets a new tag.

| File | Bytes | SHA-256 |
|---|---:|---|
{rows}
"""


def check(manifest: dict, source: str | None) -> int:
    failures = 0
    for download in all_downloads(manifest, source):
        hosts = manifest["sources"][download.source]["hosts"]
        try:
            fetch(download, hosts, None)
            print(f"  ✓ {download.source:<11} {download.label}")
        except DownloadError as error:
            failures += 1
            print(f"  ✗ {download.source:<11} {download.label}: {error}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    commands = parser.add_subparsers(dest="command", required=True)
    staging = commands.add_parser("stage", help="Fetch our release files from Hugging Face")
    staging.add_argument("--output", type=Path, required=True)
    staging.add_argument("--notes", type=Path, help="Where to write the release notes")
    staging.add_argument("--repository", default=DEFAULT_REPOSITORY, help="OWNER/NAME")
    staging.add_argument(
        "--hf-revision", help="Branch, tag or commit to read from instead of the manifest's"
    )
    checking = commands.add_parser("check", help="Download every address and verify it")
    checking.add_argument("--source", choices=("huggingface", "github"))
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    try:
        if args.command == "stage":
            print(
                f"tag={stage(manifest, args.repository, args.hf_revision, args.output, args.notes)}"
            )
            return 0
        failures = check(manifest, args.source)
    except DownloadError as error:
        print(f"  ✗ {error}", file=sys.stderr)
        return 1
    if failures:
        print(f"\n  {failures} address(es) failed.", file=sys.stderr)
        return 1
    print("\n  All addresses deliver the files of the manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
