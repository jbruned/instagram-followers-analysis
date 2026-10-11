# Follow Audit

A local web app that analyses your Instagram follower data. It reads the data exports you
download from Instagram, shows who doesn't follow you back, who you don't follow back, mutuals,
pending requests and what changed between exports, and looks up each account's public profile
(picture, live or unavailable) **without logging in**.

> **Warning: there is no authentication.** The app binds to `127.0.0.1` by default. Never expose
> it publicly or bind it to a network you don't trust: it serves your followers' data.
> `data/` and `state/` contain personal data and are gitignored. Don't commit them.

## Quick start (local)

```
uv sync
uv run follow-audit        # http://127.0.0.1:8000
```

1. Open http://127.0.0.1:8000. The first time there is nothing to show, so the page asks for
   your export. Drag the `.zip` Instagram sent you (or just the followers and following `.json`
   or `.html` files, or a whole unzipped folder) onto the page, or use **Choose files**.
2. Later, the **Upload** button (top right) does the same, and dropping files anywhere on the page
   works too. Imported uploads are kept in `data/uploads/`.

You can also put exports (`.zip` files or unzipped folders) straight in `data/`. They are imported
on startup, and new ones are found and imported automatically (the page refreshes itself and
shows a short message).

The folder is polled every 10 seconds (`FA_WATCH_INTERVAL_SECONDS`, or Settings; 0 turns it off)
rather than using OS file events, because events are not delivered reliably for Docker bind
mounts. A new file must look the same for two polls before it is read, so a zip that is still
being copied is not imported half-written.

The account an export belongs to is taken from its name (`instagram-<user>-<date>-…`) or from
the `personal_information.json` inside it. If neither says (loose followers and following files,
say), the page asks: pick one of your accounts or **Add account…** and type the username.
Exports found in the data folder by a scan are not asked about; keep their original names.

Each export is one snapshot. Importing is idempotent (a SHA-256 of the relevant files), so
re-scanning never duplicates anything, even if you rename or unzip a file.

Lists are stored as intervals ("followed you from snapshot 3 to 7"), not as a copy per snapshot, so
an import only writes what changed and any snapshot is rebuilt with one query. A list missing from
an export keeps its last known state. Exports must be imported in date order.

**Partial exports.** An export whose followers are cut to a date range (a daily export that only
holds the last day, say) cannot show who unfollowed you, so it is held back with a warning and an
explanation of how to request an **All time** export. The first export of an account is judged by
`following.json`, which is always complete: a first follower years newer than the first followed
account means the followers were cut. Later ones are compared with the followers already known:
a complete list still holds nearly all of them. **Import anyway** keeps it: it only adds people
(nobody is marked as having left), the snapshot is flagged "partial", and History says that
unfollowers in that period can't be detected. A scan of the data folder never imports a partial
export on its own; upload it to import it anyway.

A snapshot older than `FA_OLD_SNAPSHOT_DAYS` (7, also in the settings dialog) gets an "Old snapshot" warning. Its link
opens the same export steps the first-run screen shows.

Snapshots are managed from the list icon in the header: a table of every snapshot with its
account, date, import time and type (All-time or Partial), an icon to delete each one and a button to add more.
Deleting one (or `DELETE /api/snapshots/{id}`) mends the intervals around it, and the
export is remembered so a scan doesn't import it again, while uploading it by hand does.

The database schema is versioned (`PRAGMA user_version`, currently 1); an upgrade copies the
database next to itself (`*.before-version-N`) before it is applied.

### Getting an export

Instagram → Settings → Accounts Center → Your information and permissions → Download your
information → **Followers and following**, date range **All time**, format **JSON**. Request one
every few months to build up history.

JSON (current and 2021 layouts), HTML, and the 2020 `connections.json` formats are supported.

### Configuration

Environment variables (or a `.env` file in the repo root):

| Variable | Default | Purpose |
|---|---|---|
| `FA_DATA_DIR` | `./data` | Where exports are read from. Uploads are copied into `uploads/` here, so it must be writable |
| `FA_STATE_DIR` | `./state` | SQLite database and avatar cache |
| `FA_HOST` / `FA_PORT` | `127.0.0.1` / `8000` | Bind address (`--host` / `--port` override them). Anything but this machine exposes your data: there is no login |
| `FA_ALLOWED_HOSTS` | empty | Extra host names the app answers to, comma separated (see Security) |
| `FA_CHECKER_ENABLED` | `true` | Run the background account checker |
| `FA_CHECK_INTERVAL_SECONDS` | `3` | Base delay between Instagram requests |
| `FA_CHECK_JITTER_SECONDS` | `3` | Random extra delay per request |
| `FA_DAILY_REQUEST_CAP` | `5000` | Hard cap on Instagram requests per rolling 24 h |
| `FA_VIEWPORT_CHECK_DAYS` | `10` | Accounts you look at are checked sooner if never checked or older than this |
| `FA_VIEWPORT_DWELL_SECONDS` | `1` | How long a row must stay in view to count as looked at |
| `FA_SNOOZE_DAYS` | `30` | Default snooze duration |
| `FA_INACTIVE_AFTER_DAYS` | `365` | An account whose last post is older than this is badged Inactive |
| `FA_OLD_SNAPSHOT_DAYS` | `7` | A snapshot exported longer ago than this shows an Old snapshot warning |
| `FA_HIGH_FOLLOWER_RATIO` | `10` | An account with more than this many times more followers than accounts it follows (and at least 1,000 followers) gets a microphone icon |
| `FA_REQUEST_TIMEOUT_SECONDS` | `30` | Timeout for each Instagram request |
| `FA_COOLDOWN_BASE_MINUTES` / `FA_COOLDOWN_MAX_HOURS` | `15` / `4` | Cooldown after Instagram pushes back (doubles up to the maximum) |
| `FA_RECHECK_LIVE_DAYS` | `14` | Re-check live accounts after this many days |
| `FA_RECHECK_UNAVAILABLE_DAYS` | `3` | Re-check unavailable accounts after this many days |
| `FA_SCAN_ON_STARTUP` | `true` | Ingest new exports on startup |
| `FA_WATCH_INTERVAL_SECONDS` | `10` | How often the data folder is checked for new exports (0 = off) |

These are defaults. Anything saved in the Settings dialog overrides them.

## What each tab means

Tabs have sub-tabs; the number on each is the number of accounts in it.

- **Not following you back**: you follow them, they don't follow you. **Pending review**
  (nothing decided yet), **Ignored** (for good or snoozed) and **Flagged to unfollow**.
- **You don't follow back**: they follow you, you don't follow them. Same split, with
  **Flagged to follow**.
- **Mutual**: you follow each other. **All**, or only your **Close friends**.
- **Unfollowed**: **Unfollowed** (accounts reported by Instagram's export as recently unfollowed,
  plus accounts you flagged that a newer export no longer lists among the accounts you follow,
  newest first; the flagged ones carry an "Unfollow confirmed" tag) and **Unavailable accounts**: every account the checker could not find, except
  pending follow requests, which have their own list. Instagram doesn't say whether an account
  was **deactivated, banned, or renamed**, and neither can this app. These accounts (including
  ones only *possibly* unavailable after a single result) are left out of the other lists, the
  totals, headline and bars; if a later check finds one live again, it returns to its normal
  place. An account is never in two lists of a tab.
- **Follow requests**: requests you **Sent**, requests you **Received**, and **Unavailable
  accounts** (requests, in either direction, with accounts that no longer exist).
- The number on a tab adds up all its sub-tabs, including ignored and snoozed accounts.
- **History**: differences between consecutive snapshots of the same account: lost and new
  followers, accounts you followed or unfollowed, and re-follows. It appears once you have two
  snapshots of the same account.

### Ignore and flag

Each account row has up to two buttons.

- **Ignore** asks one question: ignore this account **for good**, or **snooze it for N days**
  (the number is in Settings). Either way it moves to the Ignored sub-tab of the list it was in. While snoozed, the account
  is also left out of the account checker. **Unignore** puts it back with no question.
- **Unfollow** (for accounts you follow) or **Follow** (for accounts that follow
  you) flags the account and moves it to Flagged to unfollow or Flagged to follow. The first time you do
  this a short message explains that the app can't do it for you, and offers Undo. **Unmark**
  removes the flag.

An unfollow flag shows **Unfollow pending** while the account is still in your list of accounts
you follow, and **Unfollow confirmed** once a newer export no longer lists it (it then moves to
Unfollowed using this app). Its date is the unfollow date when Instagram's export gives one,
otherwise the day you flagged it. A follow flag shows "Follow pending", then "Followed 3 days ago"
once the account is in your following list (you find it in Mutual). If an account disappears
because it was deactivated, it moves to the Unavailable tab instead.

An account has one decision at a time: choosing one replaces the others.

Decisions are stored per Instagram account (the owner of the export you are viewing), in a
`decisions` table, while profile data (picture, live or unavailable) is shared. Ignoring someone
for one of your accounts doesn't hide them in another.

Accounts you decided about leave the pending list and appear under the Ignored or Flagged
sub-tab ("Ignored" includes snoozed ones). The other tabs are not affected by these decisions.
The tab counts describe what the list shows by default; the summary bars and headline still
count the whole snapshot.

### Settings dialog

The **Settings** button (top right) changes the snooze duration and the checker's timings:
delay between requests, random extra delay, daily cap, request timeout, first and longest
cooldown, re-check intervals, and how long a row must stay in view before it is checked. Values
are validated, saved in the database, and apply immediately. **Default** puts back the value
from the `FA_*` environment variables (or the built-in default).

Hover a profile picture to enlarge it to its saved size (150px), or on a touch screen hold a
finger on it (a quick tap opens the profile, and holding never opens the image menu).

Dates in the lists are relative ("Followed 3 days ago · Last post 2 months ago") and
refresh by themselves; hover one for the exact date and time. Snapshot dates and the History
timeline stay as exact dates.

Tabs for files missing from an export are hidden. If your exports come from several accounts, an
account selector appears and all analysis is per account.

## Profile window

Clicking an account's picture or name opens a window with its picture, follower and following
counts (read from the profile page by the checker), its icons and badges, a coloured timeline of
how the two of you were related from your first snapshot to today (mutual, they follow you, you
follow them, neither) and one fact per line: when you followed them and they followed you (or when
that stopped, or "Never"), account type, last post, last check and so on.
Its buttons are Visit profile, Ignore and Follow/Unfollow. When something is missing and the
checker is on, the account is looked up right away: outside the queue, the pacing delay, the pause
switch and the daily cap, but not during a cooldown, because once Instagram has asked for a pause,
asking again is what it asked us not to do. A modified click (Cmd/Ctrl) still opens Instagram.

## How the checker works

The checker is one background task in the server process. It only looks at accounts in the
**latest** snapshot, in this order: not following back (skipping ignored accounts), requests sent,
you don't follow back, mutual; unchecked accounts first, then the oldest check. Accounts last
fetched with an older version of what the checker reads from a page (`CACHE_VERSION`) are fetched
again, but after everything unchecked or due.

**What was verified against Instagram (October 2026).** Logged-out requests behave like this:

- `GET /api/v1/users/web_profile_info/` returns `401 require_login` for every username, existing
  or not. It carries no signal and is not used.
- `GET /<username>/` (the profile page) works logged out, **but only when the request looks like
  an ordinary browser navigation** (`Sec-Fetch-*` and `Sec-Ch-Ua` headers). A live profile has
  `"pageID":"profilePage"` plus name, picture and flags; a missing one renders
  `"pageID":"httpErrorPage"`.
- A **blocked** request also renders `httpErrorPage`, so one error page proves nothing. Before
  trusting a "missing" result the worker fetches a known-live account (`instagram`) as a canary.
  If the canary doesn't resolve, the app treats it as a block and enters cooldown, and the account
  is left untouched.

**Rules the worker follows**

- No login, no cookies: every request uses a fresh HTTP client. No proxies or IP rotation.
- One request at a time, `FA_CHECK_INTERVAL_SECONDS` plus random jitter apart.
- Accounts that stay in view for one second are moved to the front of the queue when they were
  never checked or last checked more than `FA_VIEWPORT_CHECK_DAYS` ago. There is no "check now"
  button. This only changes the order: pacing, cooldowns and the daily cap still apply.
- The app does **not** rotate user agents, use proxies or try to get around limits. It sends one
  consistent set of headers, the ones of an ordinary page load, because Instagram answers
  logged-out requests without them with a login wall; that is compatibility, not a way past the
  limits, which are per IP anyway. If Instagram pushes back, the app backs off and waits.
- Instagram's terms of use do not allow automated collection of its pages. The checker reads
  public profile pages one at a time at a slow pace, for your own account's followers only, but
  it is your decision to run it: set `FA_CHECKER_ENABLED=false` to turn it off.
- The defaults are what has worked on the author's connection, not a documented limit: Instagram publishes no figure for
  logged-out requests, and the limit appears to be per IP address and to vary.
- Pushback signal (any answer other than 200 for a profile page, a login redirect, "wait a few
  minutes", or a failed canary): stop all requests, cool down 15 min, doubling per consecutive hit
  up to 4 h. A profile that exists and one that doesn't both answer 200, so such an answer says
  nothing about the account: it is not marked unavailable, it is left exactly as it was, and the
  check is simply retried after the cooldown. The cooldown, the
  request log (a `requests` table, pruned after 24 h), and the pause flag are stored in SQLite, so
  restarts resume correctly.
- Every request counts towards `FA_DAILY_REQUEST_CAP` (profile pages, canaries, picture downloads).
- An account only becomes **unavailable** after two consistent "missing" results at least
  `FA_RECHECK_UNAVAILABLE_DAYS` apart. The first result shows as "possibly unavailable". A later
  "live" result clears it. Ambiguous responses are recorded as `error` and retried with backoff;
  the app never guesses "unavailable".
- Profile pictures are downloaded immediately (their URLs are signed and expire) into
  `state/avatars/`. A re-check doesn't download a picture that is less than 30 days old.

### Last post and inactivity

The same profile page that gives the picture also lists the account's newest posts (plus pinned
ones), so the date of the **last post** costs no extra request. Instagram post IDs embed their
creation time; the app decodes it (checked against the dates in the posts' captions) and keeps
the newest one. It is shown on each row ("Last post 4 Oct 2026"), can be sorted on (**Last post:
newest / oldest**), and is included in the CSV.

- **Inactive** badge: the last post is older than `FA_INACTIVE_AFTER_DAYS` (default 365, also in
  Settings). **No posts** badge: the account has no posts.
- **Private accounts show no posts**, so they have no date. This is remembered, so they are not
  looked up again just for this. Accounts without a date sort last.
- Only the newest dozen posts are on the page, so the date is the newest of those, which is the
  last post. If Instagram changes the page layout this field stops filling in (the rest of the
  checker keeps working).
- Accounts that were checked before this feature existed are filled in over time: public ones
  are re-checked after everything never checked yet, or sooner when you scroll past them.
  Private accounts are not re-fetched, since they can't show posts anyway.

Instagram changes this behaviour often. If the checker stops finding anything, run
`uv run python scripts/probe_instagram.py <username>` (about 30 s between requests) and look at
`src/follow_audit/checker/client.py`, where the signals are documented.

## Security

There is no login, so the app protects itself from the browser instead: a page you visit can
otherwise send requests to `localhost`. Requests must name this machine in `Host` (`localhost`,
`127.0.0.1`, `[::1]`, or what `FA_ALLOWED_HOSTS` lists), which stops DNS-rebinding; requests that
change something must come from the app's own page, which stops cross-site forms; and responses
carry a strict Content-Security-Policy, `X-Frame-Options` and `nosniff`. Uploads are limited
(1 GB per file; inside a zip 100 MB per file and 400 MB in all), so a booby-trapped zip is refused
instead of filling memory. Profile pictures are only fetched from Instagram's own image hosts.
If you put the app behind a reverse proxy under another name, add that name to
`FA_ALLOWED_HOSTS`, and add your own login in the proxy.

## Backups and cleanup

Everything the app keeps is in `state/` (the database and `avatars/`) and `data/` (your exports,
including `data/uploads/`). Copy both folders to back up; stop the app first or use SQLite's
backup, because the database runs in WAL mode. To restore, put them back. `state/` also gets
a safety copy (`*.before-version-N`) when the database is upgraded;
delete them once you have checked the result. `state/avatars/` only grows with the accounts
you see; it can be deleted and is refilled by the checker.

## Running it for a long time

`uv run follow-audit` logs to the terminal (`--port 8001` picks another port when 8000 is
taken). To keep it running, use a service manager (a launchd or systemd unit running
`uv run follow-audit` in this folder, or `docker compose up -d`); stop it with Ctrl+C or the
service manager. An upload that was cut short by stopping the app leaves nothing behind: the
staging folder is cleared at startup, and an import is all or nothing.

## Limitations

- Each export is a snapshot with **one timestamp per relationship**. Earlier follow/unfollow
  cycles are only visible if they happened between two exports you kept.
- Exports carry no stable user ID, so a username change looks like one account leaving and
  another arriving. Where the checker has an Instagram user ID for both, they are matched and
  shown as "Probably renamed".
- A lost follower whose account is now unavailable is labelled "account unavailable" instead of
  "unfollowed you".
- Lists come only from exports. The app never scrapes follower lists and never follows or
  unfollows anyone; it only links to profiles.

## Privacy

- The UI loads nothing from outside: no CDNs, fonts, analytics or telemetry.
- Outbound requests go only to Instagram, and only from the checker (public profile pages and
  profile pictures). Disable it with `FA_CHECKER_ENABLED=false`.
- Export contents are never logged; usernames are only logged at debug level.
- Exports are read in memory and never extracted to disk. A copy of every export you upload is
  kept as it came in under `data/uploads/`, which includes files with more personal data than the
  app uses (such as `personal_information.json`). Delete them there if you don't want to keep them.
- Profile pictures, names and counts of the accounts in your lists are stored in `state/`.
  Nothing is encrypted: protect the folder as you would the export itself.
- Before committing anything, check `git status`: `data/` (except `.gitkeep`), `state/` and `*.db`
  must never be committed. `.gitignore` already excludes them.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the layout, ground rules and the list of
pending work.

## Docker

A `Dockerfile` and `docker-compose.yml` are included (`docker compose up`, UI on
`127.0.0.1:8000`, `./data` for exports and uploads, `./state` for persistence). Both folders must
be writable by uid 10001 (the app's user in the image): on Linux run `chown -R 10001 data state`
first. Checked end to end in October 2026 with Docker 24 (build, health check, upload into the writable
data folder, restart with the data kept, foreign Host refused).
