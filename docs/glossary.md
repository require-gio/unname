# Glossary

Every German term used in this codebase, and the English name code must use
for it. Use it in two ways:

- **Naming code.** Files, directories, identifiers, i18n keys, test names and
  comments use the English column. If a term is missing, add it here first,
  in the same PR that needs it.
- **Translating the UI.** The German UI copy in `app/src/lib/i18n/de.ts` uses
  the German column. Each i18n key is named after the English term, so
  `review.*` keys hold the copy for the *Prüfen* step.

When the sidecar HTTP API already uses a term (routes, fields, enum values in
`app/src/lib/sidecar/api.schema.json`), the API term wins.

## Terms

| German | English | Notes |
|---|---|---|
| Eintrag | entry | |
| Prüfen (review step) | review | `pruefen/` → `review/` |
| Zurück / Rückübersetzen | restore | matches the `/restore/*` API |
| Verlauf | history | |
| Gespeichert | saved | |
| Erkennung | detection | |
| Befund | finding | |
| Frage | question | |
| Entscheidung | decision | |
| Maskierung | masking | |
| Einstellungen | settings | |
| Einrichtung | setup | route `/einrichtung` → `/setup` |
| Modelle | models | |
| Namensliste / Stammdaten | name list / master data | dir `stammdaten/` → `master-data/` |
| Begriffe | terms | |
| Muster | patterns | |
| Vorlage | template | |
| Zweitmeinung | second opinion | |
| Eingang | inputs | matches Tauri `inputs.rs` |
| Ablehnung | rejection | |
| Sicherung | backup | |
| Arbeitsstand | draft | matches `drafts.py` |
| Abnahme | acceptance | dir `abnahme/` → `acceptance/` |
| Leistung | performance | |
| Rundreise | round trip | |
| Korpus | corpus | |
| Lehrer / Schüler | teacher / student | |
| Seitentreu | page-faithful | the API literal is `faithful` |
| Seite / Fluss / Raster | page / flow / grid | |
| Warteschlange | queue | |
| Stand | state | |
| Etikett / Beschriftung | label / caption | |
| Kategorie / Art | category / kind | |
| Herkunft | origin | |
| Oberfläche | ui | |
| Hilfe | help | |
| Seitenleiste | sidebar | |
| Darstellung | appearance | |
| Fenster | window | |
| Quelle | source | |
| Prüfsumme | checksum | |
| Bausteine (dev page) | components | route `(dev)/bausteine` → `(dev)/components` |
| Hülle (smoke tests) | harness | |
| Hülle (the Tauri app around the UI) | shell | |
| Stoppwörter | stopwords | |
| Wortarten / Anschriften | word classes / addresses | |
| Ausgabe | output | |
| Textmodus | text mode | |
| `pruefen.mjs` (run all checks) | `check.mjs` | *checking the repo*, not the review step |

## Terms added while renaming the frontend

| German | English | Notes |
|---|---|---|
| Ablage | dropzone | the area where files are dropped; `dropzone.svelte.ts` |
| Bereich | section | a section of the settings dialog (`*Section.svelte`) |
| Teil | part | a part of a section or of a flow (`*Part.svelte`) |
| Ansicht | view | `ReviewView`, `RestoreView`, `SavedView`, `SetupView` |
| Hinweis | notice | `GateNotice`, `OcrNotice`; not "hint" |
| Auswahl | selection | |
| Frist | retention | how long entries are kept |
| Mitschnitt | capture | the recording of calls in the smoke-test harness |
| Nachtrag | addendum | marking something by hand that detection missed |
| Restrisiko | residual risk | |
| Auslöser | trigger | |
| Vorgabe | preset | |
| Bestandteil | component | a downloadable part of a model package |
| Baustein | component | a UI building block (dev page `(dev)/components`) |
| Texterkennung | OCR | |
| Stelle | place | a position in a document where a finding sits |
| Stufe | level | |
| Stil | style | the masking style (codes, placeholders, …) |
| Methode | method | reversible or irreversible masking |
| Zeile / Spalte | line / column | `line` also names a table row in layout code |
| Blatt | sheet | |
| Fenster | window / viewport | `window` for the app window; `viewport` for the visible range in layout code, because `window` is a browser global |
| Zurück | back / restore | `back` for navigation (button, icon); `restore` for the feature |
| Stand | state | `status` when the bare word would clash with Svelte's `$state` |
| Übermalung | overpaint | the black or white area drawn over a masked place |
| Trennstelle | hyphenation point | |
| Fachbegriff | technical term | |
| Kürzel | short code | |

## Writing names that clash

A few English names are reserved or taken, so the renamer picks another:

- JavaScript keywords: `neu`→`fresh`, `loeschen`→`erase`, `klasse`→`klass`, `paket`→`bundle`,
  `schalter`→`toggleSwitch`, `ausprobieren`→`attempt`, `fortsetzen`→`proceed`.
- Svelte runes: a variable called `state` or `effect` turns `$state` or `$effect` into a
  store read, so they become `status` and `impact`.
- Playwright's `page` fixture: in the smoke tests a document page is `docPage`.
- API types: a local type never reuses an API name (`ModelPart` next to `ModelComponent`,
  `EntryViewModel` next to `EntryView`).

## Writing the English names

- **Casing follows the language, not the term.** `entry` becomes `Entry`
  (types, components), `entries` (collections), `entry_id` (Python),
  `entry-report.ts` (TS file names).
- **Multi-word terms** join by the file's convention: `secondOpinion`,
  `second_opinion`, `second-opinion`, `SecondOpinionSection.svelte`.
- **Review vs. check.** *Review* is the user's step of going through
  findings. *Check* is a repo or CI check. Never use one for the other.

## What stays German

These are data, not names. Do not translate them:

- UI copy: the values in `de.ts`.
- German word lists and their contents (stopwords, master data).
- Regexes and names of German formats: IBAN, USt-IdNr., Steuernummer, PLZ.
- Text in test fixtures and `testdata/`.

[AGENTS.md](../AGENTS.md) has the language guidelines.
