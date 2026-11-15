# Stopwords

Word lists for the post-filters of detection:

- `de_business.txt` — German business terms that NER regularly misclassifies as
  person names (*Muster, Rechnung, Angebot, …*)
- `de_sentence_start.txt` — sentence-initial words that the suspicion check must
  exempt (*Sehr, Mit, Bitte, Vielen, Anbei, Betreff, …*)
- `en_business.txt`, `en_sentence_start.txt` — the same for English (language
  pack `en`). Which list applies is decided by the language of the paragraph.

The contents are German or English language data and are not translated.

The mapping from language code to files lives only in the respective
`LanguagePack` in `sidecar/src/presidio_sidecar/languages.py`. The loader
`detection/wordlists.py` contains no word copies of its own and no
language-dependent case distinctions. The NER post-filter and the suspicion
check use the lists of the paragraph's language; business terms are not united
across all enabled languages.

## Usage

```python
from presidio_sidecar.detection import DetectionPipeline, load_language_wordlists

english = load_language_wordlists("en")
english.business_terms
english.sentence_starters

# English word lists for direct pipeline calls:
pipeline = DetectionPipeline(language="en")
```

`load_business_terms(language="en")` and `load_sentence_starters(language="en")`
also load single lists. Without a language, the default language from the
language pack registry applies (currently German). In the app, the settings
determine the active document languages; for mixed documents the choice is made
per paragraph.

The files hold one term per line. Blank lines and comment lines starting with
`#` are ignored. Unicode, case and repeated whitespace are normalized
uniformly.

A custom file can be passed as the first parameter. It replaces the default list
completely; a deliberately empty file stays empty. If the custom file is
missing, the default list **of the same language** is loaded. Unknown language
codes and missing default files raise an error. There is no silent fallback from
English to German. For explicitly optional, generic lists,
`load_wordlist(path, fallback=...)` offers a separate fallback.

## Adding a language

1. Add two UTF-8 files, for example `fr_business.txt` and `fr_sentence_start.txt`.
2. Create a `LanguagePack` in `languages.py`, enter the file names in
   `business_terms_file` and `sentence_starters_file`, and register the pack in
   `PACKS`.
3. Add tests for typical terms of the language, sentence starters and mixed
   texts.

The loader, the per-paragraph processing and the check of mandatory files in the
bundle pick up the new pack automatically. The bundle build already copies the
whole `stopwords` folder.

This covers the word list support. For a fully selectable document language,
`DocumentLanguage` and the language list currently matched against the language
catalog must also be extended, and models, function words, date/number contexts
and output translations of the language pack must be set up. A new UI language is
a step of its own.
