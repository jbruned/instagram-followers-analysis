// The account checker panel, and telling it which accounts you are looking at.
import { getJSON, postJSON } from "./api.js";
import { guard, loadSummary, reloadLoaded } from "./data.js";
import { $, el } from "./dom.js";
import { fmtTime, plural } from "./format.js";
import { checker, state } from "./state.js";

/** @param {import("./types.js").CheckerStatus} c */
export function renderChecker(c) {
  checker.enabled = c.enabled;
  $("checker").hidden = !c.enabled;
  if (!c.enabled) return;
  checker.paused = c.paused;
  checker.viewportDays = c.viewport_check_days;
  checker.dwellMs = c.viewport_dwell_seconds * 1000;
  /** @type {Record<string, string>} */
  const lines = {
    running: "is running",
    idle: "is idle",
    paused: "is paused",
    cooldown: `is cooling down until ${c.cooldown_until ? fmtTime(c.cooldown_until) : "later"}`,
    cap_reached: "is waiting for its daily limit to reset",
  };
  const line = lines[c.state] || `is ${c.state}`;
  $("checker-line").textContent = `Account checker ${line}`;
  $("checker-note").textContent = c.state === "cooldown"
    ? `Instagram asked for a pause (${c.cooldown_reason || "rate limit"}). Requests resume automatically.`
    : "Fetches profile pictures and checks account existence in real time.";
  $("checker-tiers").replaceChildren(...Object.values(c.tiers).map((t) =>
    el("li", {}, t.label.replaceAll("_", " ").replace(/^./, (m) => m.toUpperCase()),
      el("progress", { max: Math.max(t.total, 1), value: t.checked, "aria-label": `${t.label} checked` }),
      `${t.checked} / ${t.total}`)));
  $("checker-usage").textContent =
    `${c.requests_24h} of ${c.daily_cap} requests used in the last 24 hours. ${plural(c.queue_size, "account", "accounts")} waiting.`;
  const last = c.last_result;
  /** @type {Record<string, string>} */
  const outcomes = { live: "profile found", missing: "profile not found",
    rate_limited: "Instagram asked for a pause", blocked: "request looked blocked, backing off",
    error: "unclear answer, will retry" };
  $("checker-last").textContent = last ? `Last lookup: ${outcomes[last.outcome] || last.outcome}.` : "";
  $("checker-toggle").textContent = c.paused ? "Resume" : "Pause";
}

export async function pollChecker() {
  if (document.visibilityState !== "visible" || $("app").hidden) return;
  try {
    /** @type {import("./types.js").CheckerStatus} */
    const c = await getJSON("/api/checker");
    renderChecker(c);
    if (c.enabled && checker.checks_done !== null && c.checks_done !== checker.checks_done) {
      // Something was checked: refresh counts and the rows on screen (new pictures, badges).
      await loadSummary();
      if (state.tab !== "history" && document.activeElement !== $("q")) await reloadLoaded();
    }
    if (c.enabled) checker.checks_done = c.checks_done;
  } catch (e) { /* the panel just keeps its last state; list errors are shown elsewhere */ }
}

// Accounts you look at for a second are checked sooner, if they were never checked or the
// last check is older than the server's "viewport_check_days". The checker's own pacing,
// cooldown and daily cap still apply, so this only changes the order.
/** @type {WeakMap<Element, ReturnType<typeof setTimeout>>} */
const dwellTimers = new WeakMap();
/** @type {Set<string>} */
const asked = new Set();
/** @type {Set<string>} */
let pendingVisible = new Set();
/** @type {ReturnType<typeof setTimeout> | undefined} */
let flushTimer;

/** @param {HTMLElement} row */
function isStale(row) {
  const days = checker.viewportDays || 10;
  const checked = row.dataset.checked;
  return !checked || Date.now() - Date.parse(checked) > days * 86400000;
}

function flushVisible() {
  const usernames = [...pendingVisible];
  pendingVisible = new Set();
  if (usernames.length) postJSON("/api/checker/visible", { usernames }).catch(() => { /* best effort */ });
}

const rowObserver = new IntersectionObserver((entries) => {
  for (const e of entries) {
    const row = /** @type {HTMLElement} */ (e.target);
    if (e.isIntersecting) {
      if (!checker.enabled || dwellTimers.has(row) || !isStale(row)) continue;
      dwellTimers.set(row, setTimeout(() => {
        dwellTimers.delete(row);
        const user = row.dataset.user;
        if (!user || asked.has(user)) return;
        asked.add(user);
        pendingVisible.add(user);
        clearTimeout(flushTimer);
        flushTimer = setTimeout(flushVisible, 300);
      }, checker.dwellMs || 1000));
    } else if (dwellTimers.has(row)) {
      clearTimeout(dwellTimers.get(row));
      dwellTimers.delete(row);
    }
  }
}, { threshold: 0.6 });

export function watchRows() {
  rowObserver.disconnect();
  document.querySelectorAll("#rows .row").forEach((r) => rowObserver.observe(r));
}

$("checker-toggle").addEventListener("click", () => guard(async () => {
  renderChecker(await (await postJSON(`/api/checker/${checker.paused ? "resume" : "pause"}`, {})).json());
}));
