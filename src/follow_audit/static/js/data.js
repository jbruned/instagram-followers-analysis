// Loading the summary, the lists and the snapshots from the server.
import { errorText, getJSON } from "./api.js";
import { renderChecker, watchRows } from "./checker.js";
import { $, $link, $select, el } from "./dom.js";
import { fmtIso, plural } from "./format.js";
import { renderHistory } from "./history.js";
import { makeRow, patchRows } from "./rows.js";
import { listQuery, ownerQuery, PAGE, qs, state, subOf } from "./state.js";
import { renderSummary } from "./summary.js";
import { renderTabs } from "./tabs.js";
import { banner } from "./toast.js";

/** @param {boolean} [append]  add the next page instead of starting over */
export async function loadList(append = false) {
  const isHistory = state.tab === "history";
  $("history").hidden = !isHistory;
  $("rows").hidden = isHistory;
  $("toolbar").hidden = isHistory;
  // Stays hidden while loading, so the rows don't shift once the data arrives.
  $("count").hidden = true;
  $("more").hidden = true;
  // Only the tabs whose meaning is easy to get wrong keep an explanation.
  const help = subOf(state.tab).help || "";
  $("help").textContent = help;
  $("help").hidden = !help;
  if (isHistory) {
    renderHistory(await getJSON(`/api/timeline?${ownerQuery()}`));
    return;
  }
  if (!append) state.offset = 0;
  /** @type {import("./types.js").ListPage} */
  const data = await getJSON(`/api/lists/${state.tab}?${listQuery({ offset: state.offset, limit: PAGE })}`);
  state.total = data.total;
  const rows = data.items.map(makeRow);
  if (append) $("rows").append(...rows); else $("rows").replaceChildren(...rows);
  watchRows();
  state.offset += data.items.length;
  $("count").textContent = data.total ? "" : "Nothing here";
  $("count").hidden = data.total > 0;
  $("more").hidden = state.offset >= data.total;
  $link("csv").href = `/api/export/${state.tab}.csv?${listQuery()}`;
}

/** Run something, showing what went wrong instead of throwing.
 * @param {() => unknown} fn */
export async function guard(fn) {
  try { banner(""); await fn(); } catch (e) { banner(errorText(e), true); }
}

export async function loadSummary() {
  /** @type {import("./types.js").Summary} */
  const s = await getJSON(`/api/summary?${qs()}`);
  const sig = JSON.stringify(s);
  if (sig === state.summarySig) return; // nothing changed: leave the page as it is
  state.summarySig = sig;
  state.summary = s;
  renderSummary(s);
  renderTabs();
}

export async function loadAll() {
  await loadSummary();
  await loadList();
}

export async function init() {
  try { renderChecker(/** @type {import("./types.js").CheckerStatus} */ (await getJSON("/api/checker"))); } catch (e) { /* optional */ }
  /** @type {import("./types.js").Owner[]} */
  const owners = await getJSON("/api/owners");
  state.owners = owners;
  $("empty").hidden = owners.length > 0;
  $("app").hidden = owners.length === 0;
  $("owner-wrap").hidden = owners.length < 2;
  $("delete-snapshot").hidden = owners.length === 0;
  if (!owners.length) { $("snapshot-wrap").hidden = true; return; }
  const ownerSel = $select("owner");
  ownerSel.replaceChildren(...owners.map((o) => el("option", { value: o.owner },
    `${o.label || "Unknown account"} (${plural(o.snapshots, "snapshot", "snapshots")})`)));
  if (state.owner === null || !owners.some((o) => o.owner === state.owner)) state.owner = owners[0].owner;
  ownerSel.value = state.owner ?? "";
  await loadSnapshots();
}

// "10 Oct 2026 (partial, latest)": the notes about a snapshot share one pair of brackets
/** @param {import("./types.js").Snapshot} s @param {boolean} latest */
function snapshotLabel(s, latest) {
  const notes = [s.partial ? "partial" : "", latest ? "latest" : ""].filter(Boolean);
  return fmtIso(s.taken_at) + (notes.length ? ` (${notes.join(", ")})` : "");
}

export async function loadSnapshots() {
  /** @type {import("./types.js").Snapshot[]} */
  const snaps = await getJSON(`/api/snapshots?${ownerQuery()}`);
  $("snapshot-wrap").hidden = snaps.length < 2;
  $("snapshot").replaceChildren(...snaps.map((s, i) =>
    el("option", { value: s.id }, snapshotLabel(s, i === 0))));
  state.snapshot = snaps.length ? snaps[0].id : null;
  await loadAll();
}

export async function reloadLoaded() {
  // Re-render the rows already shown without losing the "Show more" position.
  const shown = state.offset;
  /** @type {import("./types.js").ListPage} */
  const data = await getJSON(`/api/lists/${state.tab}?${listQuery({ offset: 0, limit: Math.min(Math.max(shown, PAGE), 500) })}`);
  patchRows(data.items);
  state.offset = data.items.length;
  $("more").hidden = state.offset >= data.total;
}

export async function copyUsernames() {
  await guard(async () => {
    /** @type {string[]} */
    const names = [];
    for (let offset = 0; ; offset += 500) {
      /** @type {import("./types.js").ListPage} */
      const d = await getJSON(`/api/lists/${state.tab}?${listQuery({ offset, limit: 500 })}`);
      names.push(...d.items.map((i) => i.username));
      if (offset + 500 >= d.total) break;
    }
    try {
      await navigator.clipboard.writeText(names.join("\n"));
      banner(`Copied ${plural(names.length, "username", "usernames")}`);
    } catch (e) {
      banner("The browser blocked clipboard access. Use Download CSV instead.", true);
    }
  });
}

let loadingMore = false;
export async function loadMore() {
  if (loadingMore || $("more").hidden || state.tab === "history") return;
  loadingMore = true;
  await guard(() => loadList(true));
  loadingMore = false;
}
