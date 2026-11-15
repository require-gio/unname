# Master data

This is where the name lists from the source system (ERP, client management,
HR) go. They are stage 0 of detection and the most effective lever of the whole
application: whoever knows their customers, clients and suppliers needs no
statistical model for them (see [docs/detection.md](../docs/detection.md)).

**These files are personal data.** They do not belong in the repository —
`.gitignore` excludes the contents of this folder except this file. On import
they are normalized and stored as AES-256-GCM ciphertext in the private app
state. The source date comes from the oldest file timestamp, not from the app
start time.

## Expected files

| File | Content |
|---|---|
| `personen.csv` | person names, one line per entry |
| `firmen.csv` | organization names, one line per entry |

The file names are part of the documented customer export format and stay
German. CSV and JSON are supported. Alternatively, a single JSON file may hold
both explicitly separated containers:

```json
{
  "persons": ["Josef Brandstetter"],
  "organizations": ["Tischlerei Brandstetter e.U."]
}
```

**Separate lists are mandatory.** A shared list would label `Meier Spielwaren
GmbH` as a person, so it is rejected fail-closed. Replacing the source file
during an import discards the whole update; the last valid state stays active.

Entries under three characters are discarded — otherwise false hits flood the
result.

## When nothing is here yet

This is the normal case of a fresh installation. The application processes
anyway: stage 0 is skipped, stages 1 to 3 — deterministic recognizers, NER and
the suspicion check — run unchanged. This is not hidden. The start view names
the affected templates and the expected paths, the diagnostics report lists
`master_data_missing`, and the review report says "Master data stage: skipped —
no lists on this device".

What is missing is the hit rate that only a match against your own lists
delivers: without master data, no model reliably recognizes `Tischlerei Josef
Brandstetter e.U.` as an organization.

**A list that exists but is unreadable is something else.** If a file here
cannot be imported safely, upload stays blocked (HTTP 503,
`master_data_unavailable`). A broken state must not pass as "just without master
data".
