"""Local PII sidecar.

The sidecar is the only process that sees document content. It binds exclusively to the
loopback address, requires a Bearer token generated per app start, and never
communicates with the network.
"""

__version__ = "0.1.0"

# Protocol version of the stdout handshake line. Bump this when the format changes so
# that an old Tauri harness does not silently misread a new sidecar.
#
# 2: API v2 (entries, catalog, settings, text mode, Apply as dry-run).
PROTOCOL_VERSION = 2
