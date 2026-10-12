# Contributing

Follow Audit is a small local app: Python 3.12, FastAPI, SQLite and a vanilla-JS page with no
build step. This file says how to work on it and lists what is still to do. The README describes
what the app does.

## Setup

```
uv sync
uv run follow-audit --port 8001     # http://localhost:8001 (use your own port while developing)
uv run pytest
uv run ruff check && uv run ruff format --check
npm ci && npx tsc                                            # type check of the page (once, then just `npx tsc`)
node --test --test-concurrency=2 tests/frontend/*.test.mjs   # browser tests (Node 22, Chrome)
uv run python tests/fixtures/build.py   # regenerate the synthetic fixture exports
```

CI (`.github/workflows/ci.yml`) runs the same checks as four parallel jobs: `lint` (ruff),
`test` (pytest), `frontend` (the type check of the page and the browser tests) and `docker` (the
image builds). Everything must pass before a commit.

## Layout

| Path | What lives there |
|---|---|
| `src/follow_audit/ingest/` | Reading exports: `discover` (zips and folders, size limits), `parse` (JSON, HTML and old layouts), `snapshot` (import, partial-export checks), `membership` (lists stored as intervals), `upload` (browser uploads) |
| `src/follow_audit/analysis.py` | Everything computed for the page: lists, rows, the profile window, History |
| `src/follow_audit/checker/` | The background account checker: `client` (profile page parsing), `worker` (queue, pacing, cooldowns), `avatars` |
| `src/follow_audit/api.py`, `app.py`, `security.py` | Routes, app factory, Host and Origin protection |
| `src/follow_audit/db.py` | Schema and migrations |
| `src/follow_audit/static/` | The page: `index.html`, `app.css` and the ES modules in `js/` (see below) |
| `tests/` | pytest suite; `tests/fixtures/` holds synthetic exports only |

## The page's modules

No build step: `index.html` loads `js/main.js` as a native ES module and the browser follows the
imports. Each module owns one concern; `main.js` only wires and starts the page.

| Module | Concern |
|---|---|
| `state.js` | Tabs, what is selected, query strings |
| `dom.js`, `format.js`, `api.js` | DOM helpers, dates and numbers, talking to the server |
| `toast.js`, `tooltip.js`, `dialogs.js`, `icons.js` | The message, tooltips, modal dialogs, small icons |
| `summary.js`, `tabs.js`, `rows.js`, `history.js` | The summary card, tabs, the account rows, the History tab |
| `data.js` | Loading the summary, lists and snapshots |
| `actions.js` | Ignore, snooze, flag and undo |
| `profile.js`, `snapshots.js`, `settings.js`, `howto.js` | The profile window and the other dialogs |
| `upload.js` | Uploading, dropping files, the account and partial-export questions |
| `checker.js`, `polling.js`, `touch.js` | The checker panel and viewport queue, polling for new exports, touch long-press |

Modules import each other in cycles (a row needs the actions, which reload the rows). That is fine
because they only use each other's functions after loading; do not run code that needs another
module at the top level of a module, except wiring in `main.js`. `window.followAudit` exposes a few
functions for the browser tests.

## Types for the page

The page is plain JavaScript, checked by TypeScript: `tsconfig.json` turns on `checkJs` and
`strict`, and `npx tsc` (no output, nothing is built) must pass. Types are written as JSDoc
comments next to the code; the shapes the server sends are in `static/js/types.d.ts` (keep it in
step with `api.py` and `analysis.py`). Conventions:

- Annotate parameters with `@param`, and say what a function returns when it is not obvious.
- Get elements with the typed helpers in `dom.js` (`$`, `$input`, `$dialog`, ...): they throw a
  clear error when an id is missing or of another kind, so the rest of the code can trust the type.
- `getJSON` returns `any`: annotate the variable that receives it with the matching type.
- Prefer narrowing (`instanceof`, early returns) to casts; use a cast only where the page's markup
  guarantees the type, and say why.

`package.json` only holds dev tooling (TypeScript); nothing from `node_modules` is served.

## Browser tests

`tests/frontend/` holds end-to-end tests that run the real app and a real headless Chrome, with no
dependencies beyond Node 22 and Chrome (found automatically, or set `CHROME_BIN`). `harness.mjs`
starts the app on a free port with a temporary state folder and a data folder seeded from the
synthetic fixtures (`FA_TEST_CMD` overrides the command that runs the app), drives Chrome over the
DevTools protocol with real mouse and keyboard events, and removes everything afterwards.
`support.mjs` has small helpers and builders for exports the fixtures lack. Each test gets a fresh
app, so tests can change data freely. Run them with the `node --test` command under Setup; they
take about half a minute.

## Ground rules

- **Privacy.** Never commit or paste a real export, `data/` or `state/`. Fixtures are synthetic.
  Tests never make real Instagram requests (they use `respx`).
- **Scope.** The checker only reads public profile pages, logged out, one request at a time. No
  login or cookies, no automatic follow or unfollow, no proxies or rate-limit evasion, no list
  scraping, nothing loaded from outside (no CDNs or fonts).
- **Local only.** There is no login. Keep the Host and Origin checks in `security.py` working, and
  do not make the app listen on a public address by default.
- **Database.** `db.SCHEMA` is the current schema and `PRAGMA user_version` its version. To change
  it, edit `SCHEMA` and add a function to `db.MIGRATIONS` (entry `i` upgrades version `i + 1` to
  `i + 2`) with a test; a database is copied to `*.before-version-N` before it is upgraded.
- **Checker cache.** Bump `CACHE_VERSION` in `checker/worker.py` when the checker starts reading a
  new field from a profile page, so older rows are fetched again, after everything unchecked or due.
- **Style.** Ruff sets the format (100 columns). Write comments that say why, not what. Messages
  and tooltips of one sentence have no full stop at the end.
- **Trying the page.** Use your own port and a copy of the database (`FA_STATE_DIR`,
  `FA_DATA_DIR`, `FA_CHECKER_ENABLED=false`), never the instance you use for real.

## Pending work

Things deliberately left out of the MVP, roughly in the order they are worth doing.

### Planned

- **More frontend test coverage.** The browser tests (below) cover the page, dialogs, actions,
  the profile window and uploads. Still untested: the checker panel and its polling (needs a fake
  checker), the viewport observer that queues rows you look at, touch long-press on pictures,
  dropping files on the page, the first-run flow with a real upload, and slow-server behaviour
  (see the loading-states item).
- **History tab redesign.**
- **Search, filters and buttons bar redesign.**
- **Animations and loading states across the app.** Add transitions and skeleton or spinner
  states wherever data loads (summary, lists, History, the snapshots table, uploads), and test
  them by simulating slow server responses (a delay switch or proxy in the test harness) so the
  app stays pleasant on a slow connection.

### Data the export already carries but the app ignores

These are parsed and stored (so nothing needs re-importing) but not shown anywhere yet:

- `blocked`: accounts you blocked. They can never follow you back, so they should not count under
  "Not following you back"; a Blocked sub-tab or a badge would also be useful.
- `restricted`, `favorites` (feed favourites), `hide_story_from`: badges in the rows and the
  profile window, maybe filters.
- `recent_requests` (recent and permanent follow requests) and `removed_suggestions` (dismissed
  suggestions): probably only worth a line in the profile window.

### Accounts and imports

- Rename or merge accounts (for example the "Unknown account" that older imports with no username
  created, or a username change).
- A scan of the data folder cannot ask which account an unnamed export belongs to, so it still
  files it under the unnamed account. It could hold such exports back and report them instead.

### Storage and cleanup

- `state/avatars` is shared by every Instagram account the app serves (one picture per username).
  Write a cleanup script: delete pictures with no `accounts` row, and pictures of accounts that
  appear in no list of any account and were last checked more than ~90 days ago.
- `state/*.before-version-N` (made before a schema upgrade) and `data/uploads/` are never pruned.
  A script that keeps the newest few backups, and a way to list or remove uploads that no
  snapshot points at any more.
- `accounts.http_status` and `accounts.last_error` are written by the checker and never shown.
  Show them in the profile window ("last check: HTTP 200", why an account is in `error`).
- `snapshots.file_list_json` is stored and not used.

### The checker

- `CACHE_VERSION` (checker/worker.py) is the version of what the checker reads from a profile
  page. Bump it when it starts reading a new field: live accounts fetched with an older version
  are fetched again, after everything that is unchecked or due.
- Queue order today: bumped accounts first, then outdated-version accounts last, otherwise by tier
  (not following back, requests, fans, mutual), unchecked before checked, oldest check first.

### If the app ever serves several people

SQLite (one process, WAL) is right for one person and for a few users sharing a machine. For
many users or several app servers: move to PostgreSQL, run the checker as its own worker with a
shared queue and a shared request budget, add real authentication, and key everything by user.
The schema is already keyed by account (`owner`).
