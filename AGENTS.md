# Notes for AI agents

unname is a German-first app with a codebase written mostly in English. These
are guidelines, not hard rules: follow them unless a task asks otherwise.

## Language

- Prefer English for file names, identifiers, comments, docstrings, test
  names, commit messages and developer docs. German in code is not an error,
  but English keeps the code readable for everyone. Match the surrounding
  code when in doubt.
- UI copy lives in `app/src/lib/i18n/de.ts` (the reference) and `en.ts`.
  Components use i18n keys instead of hard-coded text.
- German *data* stays German and is never translated: UI copy, word lists and
  stopwords, master data, regexes for German formats (IBAN, USt-IdNr., PLZ),
  example text a detector is meant to match, and the content of `testdata/`
  and test fixtures. When a comment quotes such data, quote it verbatim.
- Use the terms in [docs/glossary.md](docs/glossary.md) for domain concepts,
  and add a missing term there.

## Checks

Run before you finish a change:

```sh
node scripts/check.mjs         # full run
node scripts/check.mjs --fast  # skip builds, browser, process and Rust tests
```

See [docs/development.md](docs/development.md) for the test layers and the
known local failures that depend on installed models.

## Commits

- One concern per commit. A rename or translation commit only renames or
  translates, so it can be reviewed with `git diff -M` and a green test run.
- Move files with `git mv` so history follows them.
- Add mass renames to [.git-blame-ignore-revs](.git-blame-ignore-revs) in a
  follow-up commit.

## Comments

Explain *why*; skip comments that only repeat the code. Do not cite internal
planning documents; if the reason matters, write it inline.

## Privacy

Code, comments and documents must not be sent to remote services unless the
user asks for it. For LLM work on the code, prefer a local model.
