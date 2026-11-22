"""Absolute import entry point for the frozen PyInstaller sidecar."""

from presidio_sidecar.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
