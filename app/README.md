# app

The unname desktop app: a Tauri 2 shell (`src-tauri/`, Rust) around a static
SvelteKit 3 / Svelte 5 UI (`src/`). The UI never touches files or the network
itself; it talks to the sidecar through the shell. See
[docs/architecture.md](../docs/architecture.md) and
[docs/development.md](../docs/development.md).

## Commands

Run from this directory after `npm install`.

```sh
npm run tauri:dev       # start the app (shell + UI + sidecar from sidecar/.venv)
npm run dev             # the UI alone in a browser
npm run check           # svelte-check
npm test                # unit tests (Vitest)
npm run test:smoke      # build, then Playwright: flows, axe, reference images, offline
npm run lint            # prettier, eslint and the design-token guard
npm run gen:api         # regenerate the API types from the sidecar models
npm run build:guarded   # production build plus the check for external assets
```

`npm run test:smoke:update` regenerates the reference images; review the diff
before committing them.

## Layout

| Path               | Contents                                                                                                    |
| ------------------ | ----------------------------------------------------------------------------------------------------------- |
| `src/routes`       | routes: `(app)` (start, history, `entry/[id]/review`, `entry/[id]/restore`, …), `setup`, `(dev)/components` |
| `src/lib/features` | screens                                                                                                     |
| `src/lib/ui`       | own components on Bits UI                                                                                   |
| `src/lib/state`    | Svelte 5 rune state                                                                                         |
| `src/lib/sidecar`  | API client, generated types, gate state                                                                     |
| `src/lib/i18n`     | typed dictionary: `de.ts` is the reference, `en.ts` must be complete                                        |
| `src/lib/styles`   | design tokens (`tokens.css`); colors live nowhere else                                                      |
| `src-tauri/src`    | the shell: inputs, saving, restoring, models, sidecar supervision                                           |
| `tests/smoke`      | Playwright flows and reference images                                                                       |

UI text lives only in `src/lib/i18n/de.ts` and `en.ts`; components use i18n keys.
