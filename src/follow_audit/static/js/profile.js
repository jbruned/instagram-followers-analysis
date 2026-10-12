// The profile window.
import { askIgnore, decide, flagAccount } from "./actions.js";
import { getJSON, postJSON } from "./api.js";
import { pollChecker } from "./checker.js";
import { guard } from "./data.js";
import { openDialog } from "./dialogs.js";
import { $dialog, $, el, present } from "./dom.js";
import { colorFor, countText, fmtDate, fmtIso } from "./format.js";
import { nameIcon } from "./icons.js";
import { ratioTip, statusBadges } from "./rows.js";
import { checker, qs } from "./state.js";

const profileDlg = $dialog("profile");

/** @type {Record<import("./types.js").RelationState, string>} */
const STATE_LABEL = {
  mutual: "You follow each other",
  fan: "They follow you, you don't follow them",
  following: "You follow them, they don't follow you",
  none: "Not following each other",
};
/** @param {string} u */
const profileApi = (u) => `/api/profile/${encodeURIComponent(u)}?${qs()}`;
/** @type {import("./types.js").Profile | null} */
let profileCurrent = null; // the profile shown, so the buttons act on it
let profileToken = 0; // the newest open wins when a slow lookup comes back late

/** A placeholder line for text that is still loading.
 * @param {string} width */
function skel(width) {
  return el("span", { class: "skel", style: `width:${width}`, "aria-hidden": "true" });
}

// One fact per line. "You followed them" and "They followed you" are always there: the date if
// it is true now, else when it stopped if known ("You unfollowed them"), else "Never".
/** @param {import("./types.js").Profile} p */
function relationFacts(p) {
  const r = p.relation;
  /** @param {string} kind */
  const lastEvent = (kind) => [...p.timeline.events].reverse().find((e) => e.kind === kind);
  /** @param {import("./types.js").RelationEvent} e */
  const between = (e) => `between ${fmtIso(e.from)} and ${fmtIso(e.to)}`;
  /**
   * @param {boolean} now
   * @param {number | null} since
   * @param {string} offKind
   * @param {number | null} offAt
   * @param {string} onLabel
   * @param {string} offLabel
   * @returns {[string, string]}
   */
  const side = (now, since, offKind, offAt, onLabel, offLabel) => {
    if (now) return [onLabel, since ? fmtDate(since) : "Yes"];
    const e = lastEvent(offKind);
    if (offAt) return [offLabel, fmtDate(offAt)];
    if (e) return [offLabel, between(e)];
    return [onLabel, "Never"];
  };
  /** @type {[string, string][]} */
  const facts = [
    side(r.following, r.following_since, "you_unfollowed", r.you_unfollowed_at,
      "You followed them", "You unfollowed them"),
    side(r.follows_you, r.follows_you_since, "they_unfollowed", null,
      "They followed you", "They unfollowed you"),
  ];
  if (r.pending_sent) facts.push(["Follow request", "Waiting for them to accept"]);
  if (r.pending_received) facts.push(["Follow request", "They asked to follow you"]);
  if (p.close_friend) facts.push(["Close friend", "Yes"]);
  if (p.is_private !== null) facts.push(["Account", p.is_private ? "Private" : "Public"]);
  if (p.is_verified) facts.push(["Verified", "Yes"]);
  if (p.last_post_at) facts.push(["Last post", fmtIso(p.last_post_at)]);
  else if (p.no_posts) facts.push(["Last post", "No posts yet"]);
  if (p.first_unavailable_at) facts.push(["First seen unavailable", fmtIso(p.first_unavailable_at)]);
  if (p.checked_at) facts.push(["Last checked", fmtIso(p.checked_at)]);
  return el("dl", { class: "facts" }, facts.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
}



// Which dates get a label under the timeline: the start and each change of state, as many as fit
// without touching, and always "Today". Widths are estimates, since the window may not be open yet.
const AXIS_LABEL_PX = 76;
const AXIS_TODAY_PX = 44;
const AXIS_GAP_PX = 10;

/**
 * @param {string[]} dates
 * @param {number} start
 * @param {number} total
 * @param {number} width
 * @returns {{ at: number, text: string, edge: string }[]}
 */
function axisLabels(dates, start, total, width) {
  const labelled = [{ at: 0, text: fmtIso(dates[0]), edge: "left" }];
  let right = AXIS_LABEL_PX;
  const limit = width - AXIS_TODAY_PX - AXIS_GAP_PX;
  for (const d of dates.slice(1)) {
    const x = total ? ((Date.parse(d) - start) / total) * width : 0;
    if (x - AXIS_LABEL_PX / 2 < right + AXIS_GAP_PX || x + AXIS_LABEL_PX / 2 > limit) continue;
    labelled.push({ at: x / width, text: fmtIso(d), edge: "" });
    right = x + AXIS_LABEL_PX / 2;
  }
  labelled.push({ at: 1, text: "Today", edge: "right" });
  return labelled;
}

/** @param {import("./types.js").Timeline} t */
function renderTimeline(t) {
  const segs = t.segments.map((s) => ({ ...s }));
  // The last snapshot's state is taken to go on until today, and today counts as one more day
  // of the line: even a snapshot taken today shows its state (the end is the end of today)
  const now = new Date();
  const todayIso = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const last = segs[segs.length - 1];
  const endIso = new Date(Math.max(Date.parse(todayIso), Date.parse(last.from)) + 86400000)
    .toISOString().slice(0, 10);
  last.to = endIso;
  last.open = true; // runs to today
  const start = Date.parse(segs[0].from);
  const total = Math.max(Date.parse(last.to) - start, 0);
  /** @param {string} iso */
  const pct = (iso) => (total ? ((Date.parse(iso) - start) / total) * 100 : 0);
  /** @param {import("./types.js").Segment} s @param {string} [link] */
  const range = (s, link = "to") => {
    if (!s.open) return `${fmtIso(s.from)}${s.to !== s.from ? ` ${link} ${fmtIso(s.to)}` : ""}`;
    return s.from === todayIso ? "Today" : `${fmtIso(s.from)} ${link} Today`;
  };
  const bar = el("div", { class: "tl-bar", role: "img",
    "aria-label": segs.map((s) => `${STATE_LABEL[s.state]}, ${range(s)}`).join("; ") },
    segs.map((s, i) => el("div", { class: `tl-seg tl-${s.state}`,
      style: total ? `left:${pct(s.from)}%;width:${pct(s.to) - pct(s.from)}%` : "left:0;width:100%",
      "data-first": i === 0 ? "1" : null,
      "data-tip": `${STATE_LABEL[s.state]}\n${range(s, "→")}` })));
  const width = Math.min(520, window.innerWidth - 24) - 44; // the dialog's inner width
  // dates are labelled only where the state changes (and at the start)
  const changes = segs.map((s) => s.from);
  const axis = el("div", { class: "tl-axis small muted" },
    axisLabels(changes, start, total, width).map((l) =>
      el("span", { class: `tl-label ${l.edge}`, style: `left:${l.at * 100}%` }, l.text)));
  const legend = el("ul", { class: "tl-legend" },
    /** @type {import("./types.js").RelationState[]} */ (["mutual", "fan", "following", "none"]).map((k) =>
      el("li", {}, el("span", { class: `tl-key tl-${k}` }), STATE_LABEL[k])));
  return el("div", {}, bar, axis, legend);
}

// loading: the account is being fetched now, so what is missing shows as a loading placeholder
/** @param {import("./types.js").Profile} p @param {{ loading?: boolean, note?: string }} [options] */
function renderProfile(p, { loading = false, note = "" } = {}) {
  profileCurrent = p;
  /** @param {unknown} v @param {string} width */
  const missing = (v, width) => (v === null || v === undefined || v === "" ? (loading ? skel(width) : null) : null);
  const initial = p.username.replace(/^[._]+/, "").charAt(0).toUpperCase() || "?";
  const avatar = p.avatar_url
    ? el("div", { class: "avatar big", style: `background:${colorFor(p.key)}` },
        el("img", { src: p.avatar_url, alt: "", draggable: "false", onerror: (/** @type {Event} */ e) => {
          const target = /** @type {HTMLElement} */ (e.target);
          const box = /** @type {HTMLElement} */ (target.parentElement); // a picture that can't be loaded gives way to the letter
          target.remove();
          box.textContent = initial;
        } }))
    : el("div", { class: "avatar big", style: `background:${colorFor(p.key)}` }, initial,
        loading ? el("span", { class: "spinner", role: "status", "aria-label": "Loading the profile picture" }) : null);
  /** @param {string} label @param {number | null} n */
  const stat = (label, n) => el("div", { class: "stat" },
    el("strong", {}, n === null || n === undefined ? (loading ? skel("2.2em") : "—") : countText(n)), label);
  const badges = statusBadges(p);
  const nameLine = el("div", { class: "name-line" },
    el("h2", { id: "profile-name" }, p.username),
    p.is_private ? nameIcon("private") : null,
    p.is_verified ? nameIcon("verified") : null,
    p.close_friend ? nameIcon("close") : null,
    p.high_ratio ? nameIcon("reach", ratioTip(p)) : null);
  $("profile-body").replaceChildren(...present([
    el("div", { class: "profile-head" }, avatar,
      el("div", { class: "who" }, nameLine,
        p.full_name ? el("div", { class: "sub" }, p.full_name)
          : (missing(null, "9em") ? el("div", { class: "sub" }, skel("9em")) : null),
        badges.length ? el("div", { class: "badges" }, badges) : null)),
    el("div", { class: "stats" }, stat("Following", p.following_count), stat("Followers", p.followers_count)),
    note ? el("p", { class: "small profile-note" }, note) : null,
    relationFacts(p),
    renderTimeline(p.timeline)]));
  const flagKind = p.flag || p.flag_action;
  const ignoredNow = p.ignored || Boolean(p.snoozed_until);
  const visit = el("a", { class: "button", href: `https://www.instagram.com/${encodeURIComponent(p.username)}/`,
    target: "_blank", rel: "noopener noreferrer" }, "Visit profile");
  const ignoreBtn = el("button", { type: "button", onclick: async () => {
    if (ignoredNow) await decide(p, "none", "back in your lists"); else await askIgnore(p);
    await refreshProfile();
  } }, ignoredNow ? "Unignore" : "Ignore");
  const flagBtn = flagKind ? el("button", { type: "button", onclick: async () => {
    if (p.flag) await decide(p, "none", "back in your lists"); else await flagAccount(p, flagKind);
    await refreshProfile();
  } }, p.flag ? "Unmark" : flagKind.charAt(0).toUpperCase() + flagKind.slice(1)) : null;
  $("profile-actions").replaceChildren(...present([visit, el("span", { class: "spacer" }), ignoreBtn, flagBtn]));
}

async function refreshProfile() {
  if (!profileCurrent || !profileDlg.open) return;
  try { renderProfile(await getJSON(profileApi(profileCurrent.username))); } catch (e) { /* keep what is shown */ }
}

// Opened with what we have; if something is missing and the checker is on, the account is
// looked up right away (outside the checker's queue and pacing, but never during a cooldown).
/** @param {string} username */
export async function openProfile(username) {
  const token = ++profileToken;
  await guard(async () => {
    const p = await getJSON(profileApi(username));
    if (token !== profileToken) return;
    const stale = !p.checked_at || Date.now() - Date.parse(p.checked_at) > 10 * 60000;
    const fetchNow = checker.enabled && p.incomplete && stale;
    renderProfile(p, { loading: fetchNow });
    openDialog(profileDlg);
    if (!fetchNow) return;
    let note = "";
    try {
      const out = await (await postJSON(`/api/checker/fetch/${encodeURIComponent(username)}`, {})).json();
      if (out.outcome === "cooldown") {
        note = `Instagram asked for a pause, so this will be fetched automatically in about ${Math.ceil(out.retry_in / 60)} min`;
      } else if (out.outcome !== "live" && out.outcome !== "missing") {
        note = "Couldn't fetch this profile now, it will be tried again later";
      }
    } catch (e) { note = "Couldn't fetch this profile now, it will be tried again later"; }
    if (token !== profileToken || !profileDlg.open) return;
    renderProfile(await getJSON(profileApi(username)), { note });
    pollChecker();
  });
}
$("profile-close").addEventListener("click", () => profileDlg.close());
profileDlg.addEventListener("close", () => {
  profileToken++;
  profileCurrent = null;
});
