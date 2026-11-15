# Word preview in the review view

The review view renders Word files in the WebView. Both libraries are part of
the frontend build (no CDN, no loading at runtime) and run only on the preview
copy that the sidecar creates in memory.

| Component | Origin | License |
|---|---|---|
| `docx-preview` 0.4.1 | [VolodymyrBaydalka/docxjs](https://github.com/VolodymyrBaydalka/docxjs) | Apache-2.0 |
| `jszip` (dependency of `docx-preview`) | [Stuk/jszip](https://github.com/Stuk/jszip) | MIT or GPL-3.0 (dual); used under MIT |

The Apache-2.0 license text is at `sidecar/packaging/licenses/Apache-2.0.txt`.

Fonts: if Calibri, Cambria, Arial, Times New Roman or Courier New are missing,
the view uses the metrically equal Carlito, Caladea, Arimo, Tinos and Cousine –
installed or bundled (`@fontsource/*` 5.3.0, OFL-1.1, license texts and proof
under `THIRD_PARTY_NOTICES/fonts`).
