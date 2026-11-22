"""Absolute import entry point for the frozen PyInstaller sidecar."""

import mimetypes

# The packaged sidecar runs in the macOS App Sandbox, which denies reading the
# system MIME tables (/etc/apache2/mime.types and others). mimetypes reads them
# on first use (openpyxl does so at import time) and raises PermissionError
# instead of skipping them. The built-in table is enough, and the frozen
# sidecar stays independent of files installed on the machine.
mimetypes.knownfiles = []

from presidio_sidecar.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
