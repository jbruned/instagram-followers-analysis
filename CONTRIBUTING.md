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
node --check src/follow_audit/static/app.js
uv run python tests/fixtures/build.py   # regenerate the synthetic fixture exports
```

CI (`.github/workflows/ci.yml`) runs the same checks. Everything must pass before a commit.

## Layout

| Path | What lives there |
|---|---|
| `src/follow_audit/ingest/` | Reading exports: `discover` (zips and folders, size limits), `parse` (JSON, HTML and old layouts), `snapshot` (import, partial-export checks), `membership` (lists stored as intervals), `upload` (browser uploads) |
| `src/follow_audit/analysis.py` | Everything computed for the page: lists, rows, the profile window, History |
| `src/follow_audit/checker/` | The background account checker: `client` (profile page parsing), `worker` (queue, pacing, cooldowns), `avatars` |
| `src/follow_audit/api.py`, `app.py`, `security.py` | Routes, app factory, Host and Origin protection |
| `src/follow_audit/db.py` | Schema and migrations |
| `src/follow_audit/static/` | The page: `index.html`, `app.js`, `app.css` |
| `tests/` | pytest suite; `tests/fixtures/` holds synthetic exports only |

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

- **Frontend tests.** Turn the headless-Chrome scripts used during development (they drive the
  page over the DevTools protocol against a temporary server and database copy) into repo tests:
  dialogs lock and unlock the page, rows are patched not rebuilt, the profile window, the account
  chooser, the undo message, the snapshots table. Run them in CI.
- **History tab redesign.**
- **Search, filters and buttons bar redesign.**
- **Split `app.js`** (about 1,450 lines in one scope) into native ES modules by concern: util/dom,
  summary, rows and badges, dialogs, upload, checker panel and polling, touch handling, wiring.
  Consider splitting `analysis.py` (lists, rows, profile, history) the same way.
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
