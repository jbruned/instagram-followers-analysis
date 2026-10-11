"use strict";

const PAGE = 150;
// Tabs and their sub-tabs. A sub-tab id is the name of the list the server returns. The number
// on a tab is the sum of its sub-tabs (except one that only repeats part of another).
const TABS = [
  { id: "not_following_back", label: "Not following you back", subs: [
    { id: "not_following_back", label: "Pending review" },
    { id: "not_following_back_ignored", label: "Ignored" },
    { id: "not_following_back_flagged", label: "To unfollow" }] },
  { id: "fans", label: "You don't follow back", subs: [
    { id: "fans", label: "Pending review" },
    { id: "fans_ignored", label: "Ignored" },
    { id: "fans_flagged", label: "To follow" }] },
  { id: "mutual", label: "Mutual", subs: [
    { id: "mutual", label: "All" },
    { id: "mutual_close_friends", label: "Close friends", subset: true }] },
  { id: "unfollowed", label: "Unfollowed", subs: [
    { id: "unfollowed", label: "Unfollowed" },
    { id: "unfollowed_unavailable", label: "Unavailable accounts" }] },
  { id: "pending_sent", label: "Follow requests", requires: ["pending_sent", "pending_received"], subs: [
    { id: "pending_sent", label: "Sent" },
    { id: "pending_received", label: "Received" },
    { id: "pending_unavailable", label: "Unavailable accounts" }] },
  { id: "history", label: "History", minSnapshots: 2, subs: [{ id: "history", label: "History",
    help: "Changes between your exports. Each export is a snapshot with one date per relationship, so earlier follow and unfollow cycles are only visible if they happened between two exports you kept. A username change looks like one account leaving and another arriving, unless the account checker has seen both." }] },
];
const isUnavailableList = (id) => id.endsWith("unavailable");
const groupOf = (listId) => TABS.find((t) => t.subs.some((s) => s.id === listId));
const subOf = (listId) => groupOf(listId).subs.find((s) => s.id === listId);
const EVENTS = [
  ["lost_follower", "Lost followers"],
  ["new_follower", "New followers"],
  ["you_unfollowed", "You unfollowed"],
  ["you_followed", "You followed"],
  ["refollow_follower", "Followers who unfollowed and followed again"],
  ["refollow_following", "Accounts you unfollowed and followed again"],
  ["renamed", "Probably renamed accounts"],
];

const checker = { enabled: false, paused: false, checks_done: null, timer: null };
// The selected tab (the id of its sub-tab) lives in the URL hash, so F5 and links keep it.
function tabFromHash() {
  let id = "";
  try { id = decodeURIComponent(location.hash.slice(1)); } catch (e) { /* malformed hash */ }
  return groupOf(id) ? id : null;
}
const state = { owner: null, snapshot: null, tab: tabFromHash() || "not_following_back", q: "", sort: "newest",
  offset: 0, total: 0, summary: null, summarySig: "" };
const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

// Floating message that fades out by itself, so it never pushes the page down.
let toastTimer, toastHide;
function hideToast() {
  const b = $("banner");
  clearTimeout(toastTimer);
  b.classList.remove("show");
  toastHide = setTimeout(() => { b.hidden = true; }, 220);
}
// An open modal dialog makes the rest of the page unclickable and paints over it, so while one
// is open the message lives inside it: visible above its backdrop, and still clickable.
function placeToast() {
  const b = $("banner");
  const host = [...document.querySelectorAll("dialog[open]")].pop() || document.body;
  if (b.parentElement !== host) host.append(b);
}

// A message at the bottom of the page. With `onClick` the message itself is the button
// ("..., click to undo"), and stays a little longer.
function banner(text, isError = false, onClick = null) {
  const b = $("banner");
  clearTimeout(toastTimer);
  clearTimeout(toastHide);
  if (!text) { hideToast(); return; }
  b.textContent = text;
  b.className = isError ? "error" : onClick ? "action" : "";
  if (onClick) {
    b.tabIndex = 0;
    b.setAttribute("role", "button");
    b.onclick = () => { hideToast(); onClick(); };
    b.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); b.onclick(); } };
  } else {
    b.removeAttribute("tabindex");
    b.setAttribute("role", "status");
    b.onclick = b.onkeydown = null;
  }
  b.hidden = false;
  placeToast();
  void b.offsetWidth; // start the fade from the hidden state
  b.classList.add("show");
  toastTimer = setTimeout(hideToast, isError ? 8000 : onClick ? 7000 : 4000);
}
$("banner").addEventListener("click", hideToast);

// Hovering (or keyboard-focusing) a block or legend entry fades every other category.
const summaryCard = document.querySelector(".summary");
const catOf = (e) => e.target.closest?.("[data-cat]");
document.addEventListener("mouseover", (e) => {
  const t = catOf(e);
  if (t) summaryCard.dataset.focus = t.dataset.cat;
});
document.addEventListener("mouseout", (e) => {
  const t = catOf(e);
  if (t && !t.contains(e.relatedTarget)) delete summaryCard.dataset.focus;
});
document.addEventListener("focusin", (e) => {
  const t = catOf(e);
  if (t && e.target.matches(":focus-visible")) summaryCard.dataset.focus = t.dataset.cat;
});
document.addEventListener("focusout", (e) => { if (catOf(e)) delete summaryCard.dataset.focus; });

// One tooltip for everything with a data-tip attribute (header icons, diagram blocks). It is
// centred on its target, kept inside the window, and its arrow points at the target. Put
// data-tippos="below" on a target to prefer showing it underneath.
const tipEl = el("div", { class: "tip", role: "tooltip" });
document.body.append(tipEl);
let tipTimer;
function showTip(target) {
  // A modal dialog sits above everything else on the page, so the tip must live inside it
  const host = target.closest("dialog[open]") || document.body;
  if (tipEl.parentElement !== host) host.append(tipEl);
  tipEl.textContent = target.dataset.tip;
  tipEl.style.left = "0px";
  tipEl.style.top = "0px";
  const t = target.getBoundingClientRect();
  const w = tipEl.offsetWidth;
  const h = tipEl.offsetHeight;
  const centre = t.left + t.width / 2;
  const left = Math.min(Math.max(8, centre - w / 2), window.innerWidth - w - 8);
  const fitsAbove = t.top - h - 10 >= 8;
  const fitsBelow = t.bottom + h + 10 <= window.innerHeight - 8;
  const below = target.dataset.tippos === "below" ? fitsBelow || !fitsAbove : !fitsAbove;
  tipEl.style.left = `${left}px`;
  tipEl.style.top = `${below ? t.bottom + 10 : t.top - h - 10}px`;
  tipEl.style.setProperty("--arrow-x", `${centre - left}px`);
  tipEl.classList.toggle("below", below);
  tipEl.classList.add("show");
}
function hideTip() {
  clearTimeout(tipTimer);
  tipEl.classList.remove("show");
}
const tipTarget = (e) => e.target.closest?.("[data-tip]");
document.addEventListener("mouseover", (e) => {
  const target = tipTarget(e);
  if (!target) return;
  clearTimeout(tipTimer);
  tipTimer = setTimeout(() => showTip(target), 200);
});
document.addEventListener("mouseout", (e) => { if (tipTarget(e)) hideTip(); });
// keyboard focus only: a mouse click shouldn't leave the tooltip hanging around
document.addEventListener("focusin", (e) => {
  if (tipTarget(e) && e.target.matches(":focus-visible")) showTip(e.target);
});
document.addEventListener("focusout", (e) => { if (tipTarget(e)) hideTip(); });
document.addEventListener("pointerdown", hideTip);
window.addEventListener("scroll", hideTip, { passive: true });

async function api(path, options) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (e) {
    throw new Error("Couldn't reach the server. Check that Follow Audit is still running, then reload.");
  }
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).detail || ""; } catch (e) { /* not JSON */ }
    const err = new Error(`The server answered ${res.status}${detail ? ": " + detail : ""}. Check the server log.`);
    err.detail = typeof detail === "string" ? detail : "";
    throw err;
  }
  return res;
}
const getJSON = async (path) => (await api(path)).json();

// "owner=..." for the endpoints that only need the account
function ownerQuery(owner = state.owner) {
  return owner !== null ? new URLSearchParams({ owner }).toString() : "";
}

function qs(extra = {}) {
  const p = new URLSearchParams();
  if (state.owner !== null) p.set("owner", state.owner);
  if (state.snapshot !== null) p.set("snapshot", state.snapshot);
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  return p.toString();
}

const fmtDate = (ts) => new Date(ts * 1000).toLocaleDateString("en-GB",
  { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const fmtIso = (iso) => fmtDate(Date.parse(iso + (iso.length === 10 ? "T00:00:00Z" : "")) / 1000);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// Relative dates: "just now", "15 hours ago", "yesterday", "2 months ago", "1 year ago", and
// "in 29 days" / "tomorrow" for the future. Exact times are shown in tooltips.
const relFormat = new Intl.RelativeTimeFormat("en", { numeric: "always" });
function relTime(ms) {
  const diff = ms - Date.now();
  const sec = Math.abs(diff) / 1000;
  if (sec < 45) return "just now";
  const min = sec / 60, hours = min / 60, days = hours / 24;
  let value, unit;
  if (min < 60) [value, unit] = [Math.max(1, Math.floor(min)), "minute"];
  else if (hours < 24) [value, unit] = [Math.floor(hours), "hour"];
  else if (days < 30) [value, unit] = [Math.floor(days), "day"];
  else if (days < 365.25) [value, unit] = [Math.max(1, Math.floor(days / 30.4375)), "month"];
  else [value, unit] = [Math.floor(days / 365.25), "year"];
  if (unit === "day" && value === 1) return diff < 0 ? "yesterday" : "tomorrow";
  return relFormat.format(diff < 0 ? -value : value, unit);
}
const fmtExact = (ms) => new Date(ms).toLocaleString("en-GB",
  { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

// "Followed 3 days ago" with the exact date on hover. The text is refreshed once a
// minute (refreshRelativeDates), so a page left open never shows stale ages.
function whenPart(label, ms) {
  const span = el("span", { class: "when", "data-tip": fmtExact(ms) });
  span.dataset.label = label;
  span.dataset.ms = String(ms);
  span.textContent = `${label} ${relTime(ms)}`;
  return span;
}
function refreshRelativeDates() {
  document.querySelectorAll(".when[data-ms]").forEach((span) => {
    span.textContent = `${span.dataset.label} ${relTime(Number(span.dataset.ms))}`;
  });
}

function colorFor(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.codePointAt(0)) % 360;
  return `hsl(${h} 45% 40%)`;
}

function profileLink(name, extra = {}) {
  return el("a", { href: `https://www.instagram.com/${encodeURIComponent(name)}/`, target: "_blank",
    rel: "noopener noreferrer", ...extra }, name);
}

// ------------------------------------------------------------ rendering

function snapshotAgeDays(takenAt) {
  return Math.floor((Date.now() - Date.parse(`${takenAt}T00:00:00Z`)) / 86400000);
}

// The export steps live once in a <template>; the first-run card and the dialog both show them.
for (const slot of document.querySelectorAll("[data-howto]")) {
  slot.replaceChildren($("howto-steps").content.cloneNode(true));
}
// An open dialog locks the page behind it. `close` fires for Cancel, Save, the X, a backdrop
// click and Escape alike, and does not bubble, hence the capture.
function openDialog(dlg) {
  if (!dlg.open) dlg.showModal();
  document.documentElement.classList.add("modal-open");
  placeToast();
}
document.addEventListener("close", () => {
  if (!document.querySelector("dialog[open]")) document.documentElement.classList.remove("modal-open");
  placeToast(); // out of a dialog that just closed
}, true);

const howtoDlg = $("howto");
function openHowto() { openDialog(howtoDlg); }
$("howto-close").addEventListener("click", () => howtoDlg.close());
$("howto-pick").addEventListener("click", () => { howtoDlg.close(); fileInput.click(); });

// What a partial snapshot cannot tell you, and the way to avoid it
function partialText(snap) {
  const lead = snap.partial_since
    ? `Its followers list only goes back to ${fmtIso(snap.partial_since)}`
    : "Its followers list is incomplete";
  const period = snap.previous_taken_at
    ? `between ${fmtIso(snap.previous_taken_at)} and ${fmtIso(snap.taken_at)}`
    : `before ${fmtIso(snap.taken_at)}`;
  return [`${lead}, so anyone who unfollowed you ${period} won't show up. We encourage you to `,
    el("button", { type: "button", class: "linklike", onclick: openHowto }, "get a new snapshot"),
    `; make sure to choose "all-time" when exporting to avoid this.`];
}

function renderSummary(s) {
  $("headline").textContent = s.headline;
  const snap = s.snapshot;
  const note = $("partial-note");
  note.hidden = !snap.partial;
  note.replaceChildren(...(snap.partial ? [el("strong", {}, "Partial export. "), ...partialText(snap)] : []));
  const age = snapshotAgeDays(snap.taken_at);
  const old = $("old-note");
  old.hidden = !(age > s.old_snapshot_days);
  old.replaceChildren(...(old.hidden ? [] : [el("strong", {}, "Old snapshot. "),
    `The data being displayed was exported ${plural(age, "day", "days")} ago so it might be outdated. We encourage you to `,
    el("button", { type: "button", class: "linklike", onclick: openHowto }, "get a new snapshot"), "."]));
  $("n-following").textContent = s.following;
  $("n-followers").textContent = s.followers;
  // The diagram and legend describe the whole snapshot; tab counts leave out snoozed accounts.
  const c = s.totals;
  const mutual = c.mutual;
  fillVenn(c, s.following, s.followers);
  const tabFor = { mutual: "mutual", nfb: "not_following_back", fans: "fans" };
  const legend = $("legend");
  legend.replaceChildren(...[["mutual", "Mutual", mutual], ["nfb", "Not following back", c.not_following_back],
    ["fans", "You don't follow back", c.fans]].map(([cls, label, n]) =>
    el("li", { "data-cat": cls }, el("button", { type: "button", onclick: () => selectTab(tabFor[cls]) },
      el("span", { class: `dot ${cls}` }), `${label} `, el("strong", {}, n)))));
}

// One shared scale for both rows: the widest of following/followers fills the diagram. The
// mutual block is a single block spanning both rows; the red and yellow blocks sit to its right.
function fillVenn(t, nFollowing, nFollowers) {
  const venn = $("venn");
  const max = Math.max(nFollowing, nFollowers);
  // Each label starts just after the end of its own row.
  $("bars").style.setProperty("--frac-top", max ? nFollowing / max : 0);
  $("bars").style.setProperty("--frac-bottom", max ? nFollowers / max : 0);
  if (!max) { venn.replaceChildren(); return; }
  const { mutual: m, not_following_back: n, fans: f } = t;
  const rest = max - m; // equals the larger of n and f
  const pct = (x, of) => Math.round((x / of) * 1000) / 10;
  const block = (cls, tab, width, text, extra = []) => el("button", {
    type: "button", class: ["seg", cls, ...extra].join(" "), style: `width:${width}%`,
    "data-cat": cls, "data-tip": text, "data-tippos": cls === "fans" ? "below" : null, "aria-label": text,
    onclick: () => selectTab(tab) });
  const parts = [];
  if (m > 0) {
    parts.push(block("mutual", "mutual", (m / max) * 100,
      `Mutual: ${m} (${pct(m, nFollowing)}% of following, ${pct(m, nFollowers)}% of followers)`,
      [n === 0 ? "free-top" : "", f === 0 ? "free-bottom" : ""].filter(Boolean)));
  }
  if (n > 0 || f > 0) {
    parts.push(el("div", { class: "venn-col" },
      n > 0 ? block("nfb", "not_following_back", (n / rest) * 100,
        `Not following back: ${n} of ${nFollowing} following (${pct(n, nFollowing)}%)`,
        [n > f ? "longer" : "", m === 0 ? "round-left" : ""].filter(Boolean)) : null,
      f > 0 ? block("fans", "fans", (f / rest) * 100,
        `You don't follow back: ${f} of ${nFollowers} followers (${pct(f, nFollowers)}%)`,
        [f > n ? "longer" : "", m === 0 ? "round-left" : ""].filter(Boolean)) : null));
  }
  venn.replaceChildren(...parts);
}

function visibleTabs() {
  const present = state.summary.kinds_present;
  return TABS.filter((t) => (!t.requires || [t.requires].flat().some((k) => present.includes(k)))
    && (!t.minSnapshots || state.summary.snapshot_count >= t.minSnapshots));
}

// a "subset" sub-tab (Close friends) is already inside another one, so it is not added twice
const subCount = (t) => t.subs.reduce((n, sub) => n + (sub.subset ? 0 : state.summary.counts[sub.id]), 0);

// A tab opens on its first sub-tab that has accounts (the first one when all are empty).
const firstFilled = (t) => (t.subs.find((sub) => state.summary.counts[sub.id] > 0) || t.subs[0]).id;

function renderTabs() {
  const tabs = visibleTabs();
  let group = groupOf(state.tab);
  if (!group || !tabs.includes(group)) { group = tabs[0]; state.tab = group.subs[0].id; }
  if (tabFromHash() !== state.tab) history.replaceState(null, "", `#${state.tab}`);
  $("tabs").replaceChildren(...tabs.map((t) => {
    const n = t.id === "history" ? null : subCount(t);
    return el("button", { type: "button", role: "tab", id: `tab-${t.id}`,
      "aria-selected": String(t === group), onclick: () => selectTab(firstFilled(t)) },
      t.label, n === null ? null : el("span", { class: "n" }, n));
  }));
  $("subtabs").hidden = group.subs.length < 2;
  $("subtabs").replaceChildren(...group.subs.map((sub) =>
    el("button", { type: "button", role: "tab", id: `tab-${sub.id}`,
      "aria-selected": String(sub.id === state.tab), onclick: () => selectTab(sub.id) },
      sub.label, el("span", { class: "n" }, state.summary.counts[sub.id]))));
}

// Small icons next to the username. The markup is fixed (no account data), so innerHTML is safe.
const ICONS = {
  private: ['Private account',
    '<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>'],
  close: ['Close friend',
    '<circle cx="12" cy="12" r="11" fill="#1E9E57" stroke="none"/><path fill="#fff" stroke="none" transform="translate(12 12) scale(.78) translate(-12 -12)" d="M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.6L12 17.3 6.1 20.6l1.3-6.6L2.5 9.4l6.6-.8z"/>'],
  // a studio microphone: the grille bars are cut out of the capsule with a mask (__ID__ is made unique)
  reach: ['High follower ratio',
    '<mask id="__ID__"><g stroke="none"><rect width="24" height="24" fill="#fff"/>' +
    '<rect x="6" y="5.2" width="5.1" height="1.8" fill="#000"/><rect x="12.9" y="5.2" width="5.1" height="1.8" fill="#000"/>' +
    '<rect x="6" y="8.8" width="5.1" height="1.8" fill="#000"/><rect x="12.9" y="8.8" width="5.1" height="1.8" fill="#000"/></g></mask>' +
    '<rect x="7.8" y="1.4" width="8.4" height="12.9" rx="4.2" fill="currentColor" stroke="none" mask="url(#__ID__)"/>' +
    '<path d="M6 8.5V10a6 6 0 0 0 12 0V8.5M12 16.3v3.6"/>' +
    '<rect x="7" y="20.3" width="10" height="2.4" rx="1.2" fill="currentColor" stroke="none"/>'],
  verified: ['Verified account',
    '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>'],
};

let iconSeq = 0;
function nameIcon(kind, tip) {
  const [label, source] = ICONS[kind];
  const paths = source.replaceAll("__ID__", `icon-mask-${++iconSeq}`); // masks need ids that are unique on the page
  const span = el("span", { class: `name-icon ${kind}`, role: "img", "aria-label": tip || label, "data-tip": tip || label });
  span.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
  return span;
}

// Tag for an account flagged to follow or unfollow. While the flag is still to do it says
// "Unfollow pending"; once the latest export no longer lists the account among those you follow
// (or now lists it, for a follow) it says "Unfollowed 3 days ago", counted from when you flagged
// it. Flags made before the time was recorded just say "Unfollowed".
function flagBadge(r, pill) {
  const unfollow = r.flag === "unfollow";
  const cls = unfollow ? "flag-un" : "flag-fo";
  const when = r.flagged_at ? fmtExact(Date.parse(r.flagged_at)) : null;
  const flaggedOn = when ? ` on ${when}` : "";
  if (r.flag_state !== "done") {
    return pill(cls, unfollow ? "Unfollow pending" : "Follow pending",
      `Once you ${r.flag} this account and update your data, it will stop showing up here`);
  }
  if (unfollow) {
    return pill("flag-done", "Unfollow confirmed",
      `You chose to unfollow this account on this app${flaggedOn}`);
  }
  const label = "Followed";
  const tip = `You chose to follow this account on this app${flaggedOn}`;
  const span = el("span", { class: "badge flag-done", "data-tip": tip });
  if (r.flagged_at) {
    span.classList.add("when"); // refreshed together with the other relative dates
    span.dataset.label = label;
    span.dataset.ms = String(Date.parse(r.flagged_at));
    span.textContent = `${label} ${relTime(Date.parse(r.flagged_at))}`;
  } else {
    span.textContent = label;
  }
  return span;
}

function statusBadges(r) {
  const pill = (cls, text, tip) => el("span", { class: `badge ${cls}`.trim(), "data-tip": tip }, text);
  const out = [];
  if (r.ignored) {
    out.push(pill("ign", "Whitelisted", "You chose to ignore this account"));
  }
  if (r.flag) out.push(flagBadge(r, pill));
  if (r.inactive) {
    out.push(pill("inactive", "Inactive", `Last post ${fmtIso(r.last_post_at)}`));
  } else if (r.no_posts) {
    out.push(pill("inactive", "No posts", "This account has no posts"));
  }
  if (r.snoozed_until) {
    const until = Date.parse(r.snoozed_until);
    out.push(pill("snooze", `Snoozed, back ${relTime(until)}`,
      `Ignored until ${fmtExact(until)}`));
  }
  if (checker.enabled && r.status === "unchecked") {
    out.push(pill("muted", "Not checked yet", "The account checker hasn't looked this account up yet"));
  }
  if (r.status === "error") {
    out.push(pill("muted", "Check failed, will retry", "Instagram's answer wasn't clear, so this will be tried again later"));
  }
  if (r.status === "unavailable") {
    out.push(pill("warn", "Unavailable",
      "This account may have been deactivated, banned, or renamed"));
  } else if (r.status === "possibly_unavailable") {
    out.push(pill("warn", "Possibly unavailable",
      "This account may have been deactivated, banned, or renamed.\nA second check will be run in a few days to confirm this"));
  }
  return out;
}

function renderRow(r) {
  // The picture opens the profile too. The username link is the keyboard and screen-reader
  // route, so this one is left out of the tab order.
  const avatarLink = { href: `https://www.instagram.com/${encodeURIComponent(r.username)}/`,
    target: "_blank", rel: "noopener noreferrer", tabindex: "-1", "aria-hidden": "true",
    draggable: "false", onclick: openOnClick(r.username) };
  const avatar = r.avatar_url
    ? el("a", { class: "avatar", ...avatarLink }, el("img", { src: r.avatar_url, alt: "", loading: "lazy", draggable: "false" }))
    : el("a", { class: "avatar", style: `background:${colorFor(r.key)}`, ...avatarLink },
        r.username.replace(/^[._]+/, "").charAt(0).toUpperCase() || "?");
  const dateParts = [];
  if (r.ts) dateParts.push(whenPart(r.context, r.ts * 1000));
  if (isUnavailableList(state.tab)) {
    if (r.first_unavailable_at) dateParts.push(whenPart("first seen unavailable", Date.parse(r.first_unavailable_at)));
    if (r.checked_at) dateParts.push(whenPart("last checked", Date.parse(r.checked_at)));
  } else if (r.last_post_at) {
    dateParts.push(whenPart("Last post", Date.parse(r.last_post_at)));
  }
  // The parts share a line with a "·" between them when they fit; otherwise each goes on its
  // own line and the dot disappears (see .dateline in the stylesheet).
  const dateLine = dateParts.length
    ? el("div", { class: "sub dateline" }, dateParts)
    : (isUnavailableList(state.tab) ? el("div", { class: "sub" }, "Not checked yet") : null);
  const flagKind = r.flag || r.flag_action; // "follow" | "unfollow" | null
  const ignoredNow = r.ignored || Boolean(r.snoozed_until);
  const ignoreBtn = el("button", { type: "button", "aria-pressed": String(ignoredNow),
    "data-tip": ignoredNow ? "Stop ignoring this account"
      : "Whitelist this account or snooze it",
    onclick: () => (ignoredNow
      ? decide(r, "none", "back in your lists")
      : askIgnore(r)) }, ignoredNow ? "Unignore" : "Ignore");
  const flagBtn = flagKind
    ? el("button", { type: "button", "aria-pressed": String(Boolean(r.flag)),
        "data-tip": r.flag ? "Remove the flag from this account"
          : `Flag this account to ${flagKind} it later`,
        onclick: () => (r.flag ? decide(r, "none", "back in your lists") : flagAccount(r, flagKind)) },
        r.flag ? "Unmark" : flagKind.charAt(0).toUpperCase() + flagKind.slice(1))
    : null;
  const badges = statusBadges(r);
  const li = el("li", { class: "row", "data-user": r.username, "data-checked": r.checked_at || "" }, avatar,
    el("div", { class: "who" },
      el("div", { class: "name-line" },
        profileLink(r.username, { class: "uname", onclick: openOnClick(r.username) }),
        r.is_private ? nameIcon("private") : null,
        r.is_verified ? nameIcon("verified") : null,
        r.close_friend ? nameIcon("close") : null,
        r.high_ratio ? nameIcon("reach", ratioTip(r)) : null,
        badges),
      r.full_name ? el("div", { class: "sub" }, r.full_name) : null,
      dateLine),
    el("div", { class: "actions" }, ignoreBtn, flagBtn));
  return li;
}

function eventItem(kind, e) {
  const note = (text) => el("span", { class: "muted small" }, ` · ${text}`);
  const li = el("li", {}, profileLink(e.username));
  if (e.label) li.append(note(e.label));
  if (e.previous_username) li.append(note(`was ${e.previous_username}`));
  if (e.previous_ts && e.ts) li.append(note(`${fmtDate(e.previous_ts)} → ${fmtDate(e.ts)}`));
  else if (kind !== "lost_follower" && e.ts) li.append(note(fmtDate(e.ts)));
  return li;
}

function renderGroup(g) {
  const chips = EVENTS.filter(([k]) => g.counts[k]).map(([k, label]) =>
    el("span", { class: "badge" }, `${g.counts[k]} ${label.toLowerCase()}`));
  const lists = EVENTS.filter(([k]) => g.events[k].length).map(([k, label]) =>
    el("details", {},
      el("summary", {}, `${label} (${g.events[k].length})`),
      el("ul", {}, g.events[k].map((e) => eventItem(k, e)))));
  return el("section", { class: "group" },
    el("h3", {}, `Between ${fmtIso(g.from_date)} and ${fmtIso(g.to_date)}`),
    g.partial ? el("p", { class: "partial-note small" },
      "The newer snapshot is a partial export, so unfollowers in this period can't be detected") : "",
    chips.length ? el("div", { class: "chips" }, chips) : el("p", { class: "muted" }, "No changes."),
    lists);
}

function renderHistory(groups) {
  const root = $("history");
  if (!groups.length) {
    root.replaceChildren(el("p", { class: "muted" },
      "History needs at least two exports from the same account. Upload another export."));
    return;
  }
  root.replaceChildren(...groups.map(renderGroup));
}

// ------------------------------------------------------------ data loading

function listQuery(extra = {}) {
  return qs({ q: state.q, sort: state.sort, ...extra });
}

async function loadList(append = false) {
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
  const data = await getJSON(`/api/lists/${state.tab}?${listQuery({ offset: state.offset, limit: PAGE })}`);
  state.total = data.total;
  const rows = data.items.map(makeRow);
  if (append) $("rows").append(...rows); else $("rows").replaceChildren(...rows);
  watchRows();
  state.offset += data.items.length;
  $("count").textContent = data.total ? "" : "Nothing here";
  $("count").hidden = data.total > 0;
  $("more").hidden = state.offset >= data.total;
  $("csv").href = `/api/export/${state.tab}.csv?${listQuery()}`;
}

async function selectTab(id) {
  state.tab = id;
  if (tabFromHash() !== id) history.pushState(null, "", `#${id}`); // Back returns to the last tab
  renderTabs();
  await guard(loadList);
}

window.addEventListener("popstate", () => {
  const id = tabFromHash();
  if (!id || !state.summary || id === state.tab) return;
  state.tab = id;
  renderTabs();
  guard(loadList);
});

async function guard(fn) {
  try { banner(""); await fn(); } catch (e) { banner(e.message, true); }
}

async function loadSummary() {
  const s = await getJSON(`/api/summary?${qs()}`);
  const sig = JSON.stringify(s);
  if (sig === state.summarySig) return; // nothing changed: leave the page as it is
  state.summarySig = sig;
  state.summary = s;
  renderSummary(s);
  renderTabs();
}

async function loadAll() {
  await loadSummary();
  await loadList();
}

async function init() {
  try { renderChecker(await getJSON("/api/checker")); } catch (e) { /* optional */ }
  const owners = await getJSON("/api/owners");
  state.owners = owners;
  $("empty").hidden = owners.length > 0;
  $("app").hidden = owners.length === 0;
  $("owner-wrap").hidden = owners.length < 2;
  $("delete-snapshot").hidden = owners.length === 0;
  if (!owners.length) { $("snapshot-wrap").hidden = true; return; }
  const ownerSel = $("owner");
  ownerSel.replaceChildren(...owners.map((o) => el("option", { value: o.owner },
    `${o.label || "Unknown account"} (${plural(o.snapshots, "snapshot", "snapshots")})`)));
  if (state.owner === null || !owners.some((o) => o.owner === state.owner)) state.owner = owners[0].owner;
  ownerSel.value = state.owner;
  await loadSnapshots();
}

// "10 Oct 2026 (partial, latest)": the notes about a snapshot share one pair of brackets
function snapshotLabel(s, latest) {
  const notes = [s.partial ? "partial" : "", latest ? "latest" : ""].filter(Boolean);
  return fmtIso(s.taken_at) + (notes.length ? ` (${notes.join(", ")})` : "");
}

async function loadSnapshots() {
  const snaps = await getJSON(`/api/snapshots?${ownerQuery()}`);
  $("snapshot-wrap").hidden = snaps.length < 2;
  $("snapshot").replaceChildren(...snaps.map((s, i) =>
    el("option", { value: s.id }, snapshotLabel(s, i === 0))));
  state.snapshot = snaps.length ? snaps[0].id : null;
  await loadAll();
}

// ------------------------------------------------------------ actions

async function postJSON(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
}

// Ignore (for good), snooze (ignore for a while) and flag are one decision at a time: choosing
// one replaces the others. The account may disappear from this list (hidden unless ticked under
// sub-tab), so the rows on screen are reloaded in place and the counts refreshed.
// What the account was set to before, so a click on "click to undo" puts it back.
function decisionOf(r) {
  return r.ignored ? "ignored" : r.snoozed_until ? "snoozed" : r.flag || "none";
}

async function decide(r, decision, outcome, { undoable = false } = {}) {
  const before = decisionOf(r);
  await guard(async () => {
    // decisions belong to the Instagram account whose export you are looking at
    await postJSON(`/api/decisions/${encodeURIComponent(r.username)}?${ownerQuery()}`, { decision });
    await loadSummary();
    await reloadLoaded();
    if (!undoable) { banner(`${r.username}: ${outcome}`); return; }
    banner(`${r.username}: ${outcome}, click to undo`, false,
      () => decide(r, before, before === "none" ? "back in your lists" : "restored"));
  });
}

// A small question dialog. Resolves with the value of the button pressed, or null if it was
// dismissed (Escape, the Cancel button or a click on the backdrop).
const askDlg = $("ask");
function ask({ title, text, choices }) {
  return new Promise((resolve) => {
    $("ask-title").textContent = title;
    $("ask-text").textContent = text;
    let answer = null;
    $("ask-actions").replaceChildren(...choices.map((c) => el("button", {
      type: "button", class: c.primary ? "primary" : "",
      onclick: () => { answer = c.value; askDlg.close(); } }, c.label)));
    askDlg.addEventListener("close", () => resolve(answer), { once: true });
    openDialog(askDlg);
  });
}
// Every dialog closes on a click on its backdrop (the dialog element itself, since the form fills it).
document.addEventListener("click", (e) => {
  if (e.target instanceof HTMLDialogElement && e.target.open) e.target.close();
});

let snoozeDaysCache = null; // cleared when settings are saved
async function snoozeDays() {
  if (snoozeDaysCache === null) {
    try {
      const fields = (await getJSON("/api/settings")).fields;
      snoozeDaysCache = fields.find((f) => f.key === "snooze_days").value;
    } catch (e) { /* the default is used until the settings can be read */ }
  }
  return snoozeDaysCache || 30;
}

// ------------------------------------------------------------ manage snapshots

const snapsDlg = $("snapshots");
const TRASH_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6"/></svg>';

async function renderSnapshotTable() {
  const owners = await getJSON("/api/owners");
  const groups = await Promise.all(owners.map(async (o) => ({ label: o.label || "Unknown account",
    snaps: await getJSON(`/api/snapshots?${ownerQuery(o.owner)}`) })));
  $("snapshots-table").querySelector("tbody").replaceChildren(...groups.flatMap((g) =>
    g.snaps.map((s) => {
      const btn = el("button", { type: "button", class: "icon-btn snap-delete", "aria-label": "Delete snapshot",
        "data-tip": "Delete snapshot" });
      btn.innerHTML = TRASH_SVG;
      let armed = null;
      btn.addEventListener("click", () => {
        // a second click confirms; the first only arms the button for a few seconds
        if (!armed) {
          btn.classList.add("armed");
          btn.textContent = "Sure?";
          armed = setTimeout(() => {
            armed = null;
            btn.classList.remove("armed");
            btn.innerHTML = TRASH_SVG;
          }, 3000);
          return;
        }
        clearTimeout(armed);
        removeSnapshot(s, btn);
      });
      const type = s.partial ? el("span", { class: "snap-partial", "data-tip": s.partial }, "Partial") : "All-time";
      const imported = new Date(s.ingested_at).toLocaleString("en-GB", { day: "numeric", month: "short",
        year: "numeric", hour: "2-digit", minute: "2-digit" });
      return el("tr", {}, el("td", {}, g.label), el("td", {}, fmtIso(s.taken_at)),
        el("td", { class: "muted" }, imported), el("td", {}, type), el("td", {}, btn));
    })));
  return groups.reduce((n, g) => n + g.snaps.length, 0);
}

async function removeSnapshot(s, btn) {
  btn.disabled = true;
  await guard(async () => {
    await api(`/api/snapshots/${s.id}`, { method: "DELETE" });
    await init();
    if (await renderSnapshotTable() === 0) snapsDlg.close();
    banner(`Deleted the snapshot from ${fmtIso(s.taken_at)}`);
  });
  btn.disabled = false;
}

async function openSnapshots() {
  await guard(async () => {
    await renderSnapshotTable();
    openDialog(snapsDlg);
  });
}
$("snapshots-close").addEventListener("click", () => snapsDlg.close());
$("snapshots-add").addEventListener("click", () => fileInput.click());

// ------------------------------------------------------------ profile window

const profileDlg = $("profile");
const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const countText = (n) => (n === null || n === undefined ? "—" : compact.format(n));
const exactCount = (n) => (n === null || n === undefined ? "" : n.toLocaleString());

function ratioTip(r) {
  const followers = r.followers_count;
  const following = r.following_count;
  if (!following) return "Follows no one";
  const times = followers / following;
  const n = times >= 10 ? Math.round(times).toLocaleString() : times.toFixed(1);
  return `${n}x more followers than following`;
}

// A plain click on a name or picture opens the profile window; a modified click (new tab, ...)
// still goes to Instagram.
function openOnClick(username) {
  return (e) => {
    if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    openProfile(username);
  };
}

const STATE_LABEL = {
  mutual: "You follow each other",
  fan: "They follow you, you don't follow them",
  following: "You follow them, they don't follow you",
  none: "Not following each other",
};
const profileApi = (u) => `/api/profile/${encodeURIComponent(u)}?${qs()}`;
let profileCurrent = null; // the profile shown, so the buttons act on it
let profileToken = 0; // the newest open wins when a slow lookup comes back late

function skel(width) {
  return el("span", { class: "skel", style: `width:${width}`, "aria-hidden": "true" });
}

// One fact per line. "You followed them" and "They followed you" are always there: the date if
// it is true now, else when it stopped if known ("You unfollowed them"), else "Never".
function relationFacts(p) {
  const r = p.relation;
  const lastEvent = (kind) => [...p.timeline.events].reverse().find((e) => e.kind === kind);
  const between = (e) => `between ${fmtIso(e.from)} and ${fmtIso(e.to)}`;
  const side = (now, since, offKind, offAt, onLabel, offLabel) => {
    if (now) return [onLabel, since ? fmtDate(since) : "Yes"];
    const e = lastEvent(offKind);
    if (offAt) return [offLabel, fmtDate(offAt)];
    if (e) return [offLabel, between(e)];
    return [onLabel, "Never"];
  };
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

const DAY_MS = 86400000;

// Which dates get a label under the timeline: the start and each change of state, as many as fit
// without touching, and always "Today". Widths are estimates, since the window may not be open yet.
const AXIS_LABEL_PX = 76;
const AXIS_TODAY_PX = 44;
const AXIS_GAP_PX = 10;

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
  const pct = (iso) => (total ? ((Date.parse(iso) - start) / total) * 100 : 0);
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
    ["mutual", "fan", "following", "none"].map((k) =>
      el("li", {}, el("span", { class: `tl-key tl-${k}` }), STATE_LABEL[k])));
  return el("div", {}, bar, axis, legend);
}

// loading: the account is being fetched now, so what is missing shows as a loading placeholder
function renderProfile(p, { loading = false, note = "" } = {}) {
  profileCurrent = p;
  const missing = (v, width) => (v === null || v === undefined || v === "" ? (loading ? skel(width) : null) : null);
  const initial = p.username.replace(/^[._]+/, "").charAt(0).toUpperCase() || "?";
  const avatar = p.avatar_url
    ? el("div", { class: "avatar big", style: `background:${colorFor(p.key)}` },
        el("img", { src: p.avatar_url, alt: "", draggable: "false", onerror: (e) => {
          const box = e.target.parentElement; // a picture that can't be loaded gives way to the letter
          e.target.remove();
          box.textContent = initial;
        } }))
    : el("div", { class: "avatar big", style: `background:${colorFor(p.key)}` }, initial,
        loading ? el("span", { class: "spinner", role: "status", "aria-label": "Loading the profile picture" }) : null);
  const stat = (label, n) => el("div", { class: "stat" },
    el("strong", {}, n === null || n === undefined ? (loading ? skel("2.2em") : "—") : countText(n)), label);
  const badges = statusBadges(p);
  const nameLine = el("div", { class: "name-line" },
    el("h2", { id: "profile-name" }, p.username),
    p.is_private ? nameIcon("private") : null,
    p.is_verified ? nameIcon("verified") : null,
    p.close_friend ? nameIcon("close") : null,
    p.high_ratio ? nameIcon("reach", ratioTip(p)) : null);
  $("profile-body").replaceChildren(...[
    el("div", { class: "profile-head" }, avatar,
      el("div", { class: "who" }, nameLine,
        p.full_name ? el("div", { class: "sub" }, p.full_name)
          : (missing(null, "9em") ? el("div", { class: "sub" }, skel("9em")) : null),
        badges.length ? el("div", { class: "badges" }, badges) : null)),
    el("div", { class: "stats" }, stat("Following", p.following_count), stat("Followers", p.followers_count)),
    note ? el("p", { class: "small profile-note" }, note) : null,
    relationFacts(p),
    renderTimeline(p.timeline)].filter(Boolean));
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
  $("profile-actions").replaceChildren(...[visit, el("span", { class: "spacer" }), ignoreBtn, flagBtn]
    .filter(Boolean));
}

async function refreshProfile() {
  if (!profileCurrent || !profileDlg.open) return;
  try { renderProfile(await getJSON(profileApi(profileCurrent.username))); } catch (e) { /* keep what is shown */ }
}

// Opened with what we have; if something is missing and the checker is on, the account is
// looked up right away (outside the checker's queue and pacing, but never during a cooldown).
async function openProfile(username) {
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

// What you are being asked depends on the list the account is in.
function ignoreQuestion() {
  const what = { not_following_back: "this account not following you back",
    fans: "not following this account back" }[groupOf(state.tab).id] || "this account as it is";
  return `Are you okay with ${what}, or do you just want to snooze it and decide later?`;
}

async function askIgnore(r) {
  const days = await snoozeDays();
  const choice = await ask({
    title: `Ignore ${r.username}?`,
    text: ignoreQuestion(),
    choices: [
      { label: "Ignore for good", value: "ignore", primary: true },
      { label: `Snooze for ${plural(days, "day", "days")}`, value: "snooze" },
      { label: "Cancel", value: null },
    ],
  });
  if (choice === "ignore") await decide(r, "ignored", "ignored", { undoable: true });
  else if (choice === "snooze") {
    await decide(r, "snoozed", `snoozed for ${plural(days, "day", "days")}`, { undoable: true });
  }
}

// The first time you flag an account to follow or unfollow, explain what a flag is.
function seenFlagNote(kind, set) {
  try {
    if (set) localStorage.setItem(`fa-flag-note-${kind}`, "1");
    return localStorage.getItem(`fa-flag-note-${kind}`) === "1";
  } catch (e) { return false; }
}
async function flagAccount(r, kind) {
  await decide(r, kind, `flagged to ${kind}`, { undoable: true });
  if (seenFlagNote(kind)) return;
  seenFlagNote(kind, true);
  const answer = await ask({
    title: `Flagged for ${kind}`,
    text: `We can't ${kind} accounts for you. Flagged accounts collect in the To ${kind} tab, so you can ${kind} them all in one go when you finish reviewing.`,
    choices: [
      { label: "Got it", value: "ok", primary: true },
      { label: "Undo", value: "undo" },
    ],
  });
  if (answer === "undo") await decide(r, "none", "back in your lists");
}

// A row remembers what it was drawn from, so an update can tell what changed.
function makeRow(r) {
  const node = renderRow(r);
  node.dataset.sig = JSON.stringify(r);
  return node;
}

// Show these rows, touching only what changed so pictures don't flicker and a focused button
// keeps its focus while the checker updates accounts in the background.
function patchRows(items) {
  const box = $("rows");
  const old = new Map([...box.children].map((n) => [n.dataset.user, n]));
  // keep the row already on the page when what it was drawn from is unchanged
  const wanted = items.map((r) => {
    const node = old.get(r.username);
    return node && node.dataset.sig === JSON.stringify(r) ? node : makeRow(r);
  });
  const keep = new Set(wanted);
  for (const n of [...box.children]) if (!keep.has(n)) n.remove();
  // put the rows in order, moving only those that are not already in place (a moved row would
  // lose the focus of the button inside it)
  wanted.forEach((n, i) => { if (box.children[i] !== n) box.insertBefore(n, box.children[i] || null); });
  watchRows();
}

async function reloadLoaded() {
  // Re-render the rows already shown without losing the "Show more" position.
  const shown = state.offset;
  const data = await getJSON(`/api/lists/${state.tab}?${listQuery({ offset: 0, limit: Math.min(Math.max(shown, PAGE), 500) })}`);
  patchRows(data.items);
  state.offset = data.items.length;
  $("more").hidden = state.offset >= data.total;
}

async function copyUsernames() {
  await guard(async () => {
    const names = [];
    for (let offset = 0; ; offset += 500) {
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

// Upload: the header button, the first-run card and dropping files anywhere on the page.
async function uploadFiles(items, { force = false, owner = "" } = {}) {
  if (!items.length) return;
  const form = new FormData();
  for (const { file, path } of items) form.append("files", file, path || file.name);
  $("upload").disabled = true;
  banner("Importing…");
  let partial = [];
  let unowned = [];
  await guard(async () => {
    const params = new URLSearchParams();
    if (owner) params.set("owner", owner);
    if (force) params.set("force", "1");
    const out = await (await api(`/api/upload?${params}`, { method: "POST", body: form })).json();
    const parts = [];
    if (out.imported.length) parts.push(`Imported ${plural(out.imported.length, "new snapshot", "new snapshots")}`);
    else if (out.skipped) parts.push("Already imported, nothing new");
    else if (!out.errors.length && !out.partial.length && !out.needs_owner.length) parts.push("Nothing to import");
    if (out.errors.length) parts.push(`Problems: ${out.errors.join("; ")}`);
    await init();
    await syncWatch();
    banner(parts.join(". "), out.errors.length > 0);
    partial = out.partial;
    unowned = out.needs_owner;
  });
  $("upload").disabled = false;
  if (snapsDlg.open) await renderSnapshotTable().catch(() => {});
  if (unowned.length) {
    // nothing says whose these files are: ask, then send them again with the account named
    const chosen = await askAccount(unowned);
    if (chosen) await uploadFiles(items, { force, owner: chosen });
    else banner("Nothing was imported");
    return;
  }
  if (partial.length && await askPartial(partial) === "import") await uploadFiles(items, { force: true, owner });
}

const ACCOUNT_NAME = /^[A-Za-z0-9._]{1,30}$/;

// Which Instagram account do these files belong to? Resolves with a username (an existing
// account or a new one typed in) or null when dismissed.
function askAccount(unowned) {
  return new Promise((resolve) => {
    const dlg = $("account-pick");
    const owners = (state.owners || []).filter((o) => o.owner);
    const what = unowned.length === 1 && unowned[0].name !== "export" ? unowned[0].name : "These files";
    $("account-text").textContent =
      `${what} ${unowned.length === 1 && what !== "These files" ? "doesn't" : "don't"} say which Instagram account ${what === "These files" ? "they belong" : "it belongs"} to.`
      + (owners.length ? " Pick the account, or add a new one." : " Enter the account's username.");
    const NEW = "+new";
    const radio = (value, badge, title, sub) => el("label", { class: "account-opt" },
      el("span", { class: "account-badge", "aria-hidden": "true" }, badge),
      el("span", { class: "account-main" }, el("strong", {}, title), sub ? el("small", {}, sub) : null),
      el("input", { type: "radio", name: "account", value }));
    const plus = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    plus.setAttribute("viewBox", "0 0 24 24");
    plus.innerHTML = '<path d="M12 5v14M5 12h14"/>';
    $("account-options").replaceChildren(
      ...owners.map((o) => radio(o.owner, o.owner[0].toUpperCase(), o.owner,
        plural(o.snapshots, "snapshot", "snapshots"))),
      radio(NEW, plus, "Add account…", "Another Instagram account"));
    const picked = () => dlg.querySelector("input[name=account]:checked");
    const nameIn = $("account-name");
    const err = $("account-error");
    const value = () => (picked()?.value === NEW ? nameIn.value.trim().replace(/^@/, "") : picked()?.value || "");
    const refresh = (typing) => {
      const isNew = picked()?.value === NEW;
      $("account-new").hidden = !isNew;
      const v = value();
      const bad = isNew && v !== "" && !ACCOUNT_NAME.test(v);
      nameIn.setAttribute("aria-invalid", bad ? "true" : "false");
      err.hidden = !bad;
      if (bad) err.textContent = "Use 1 to 30 letters, numbers, dots or underscores.";
      $("account-ok").disabled = !(isNew ? ACCOUNT_NAME.test(v) : v !== "");
      if (isNew && !typing) nameIn.focus();
    };
    const first = owners.find((o) => o.owner === state.owner) || owners[0];
    const start = first ? first.owner : NEW;
    for (const r of dlg.querySelectorAll("input[name=account]")) {
      r.checked = r.value === start;
      r.addEventListener("change", () => refresh(false));
    }
    nameIn.value = "";
    nameIn.oninput = () => refresh(true);
    let answer = null;
    $("account-form").onsubmit = (e) => {
      e.preventDefault();
      if ($("account-ok").disabled) return;
      answer = value().toLowerCase();
      dlg.close();
    };
    $("account-cancel").onclick = () => dlg.close();
    dlg.addEventListener("close", () => resolve(answer), { once: true });
    refresh(true);
    openDialog(dlg);
    if (start === NEW) nameIn.focus();
    else dlg.querySelector("input[name=account]:checked").focus();
  });
}

// An export whose followers look cut to a date range cannot show who unfollowed you. It is held
// back until you say to keep it.
async function askPartial(items) {
  const what = items.map((p) => {
    const lead = `${p.name === "export" ? "This" : p.name} looks like a partial export: ${p.reason}.`;
    return p.first
      ? `${lead} Its followers may be incomplete, so it can't show who unfollowed you.`
      : `${lead} This partial snapshot can't detect if someone has unfollowed you since the previous snapshot (${fmtIso(p.previous)}).`;
  }).join("\n\n");
  return ask({
    title: "Partial export",
    text: `${what}\n\nTo get a complete one, request it again in Instagram: Settings, Accounts Center, Your information and permissions, Download your information, Followers and following. Choose Date range: All time and format JSON. If you use an automated export, change its date range to All time too.`,
    choices: [
      { label: "Cancel", value: null, primary: true },
      { label: "Import anyway", value: "import" },
    ],
  });
}

// Dropped folders arrive as entries; flatten them, keeping each file's path inside the folder.
async function filesOfDrop(dt) {
  const entries = [...(dt.items || [])].map((i) => i.webkitGetAsEntry?.()).filter(Boolean);
  if (!entries.length) return [...dt.files].map((file) => ({ file }));
  const out = [];
  const walk = async (entry, prefix) => {
    if (entry.isFile) {
      const file = await new Promise((ok, no) => entry.file(ok, no));
      out.push({ file, path: prefix + entry.name });
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      for (;;) {
        const batch = await new Promise((ok, no) => reader.readEntries(ok, no));
        if (!batch.length) break;
        for (const child of batch) await walk(child, `${prefix}${entry.name}/`);
      }
    }
  };
  for (const e of entries) await walk(e, "");
  return out;
}

const fileInput = $("file-input");
$("upload").addEventListener("click", () => fileInput.click());
$("pick").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  const items = [...fileInput.files].map((file) => ({ file }));
  fileInput.value = "";
  uploadFiles(items);
});

let dragDepth = 0;
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
const setDropzone = (on) => { $("dropzone").hidden = !on; };
window.addEventListener("dragenter", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth++;
  setDropzone(true);
});
window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener("dragleave", (e) => {
  if (!hasFiles(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) setDropzone(false);
});
window.addEventListener("drop", async (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  setDropzone(false);
  uploadFiles(await filesOfDrop(e.dataTransfer));
});

// New exports dropped into the data folder are imported by the server; pick them up here.
let knownImports = null;

async function syncWatch() {
  try { knownImports = (await getJSON("/api/watch")).imports; } catch (e) { /* optional */ }
}

async function pollData() {
  if (document.visibilityState !== "visible") return;
  try {
    const w = await getJSON("/api/watch");
    const changed = knownImports !== null && w.imports !== knownImports;
    knownImports = w.imports;
    if (changed) {
      await init();
      banner(w.last_import
        ? `New export found and imported (${fmtIso(w.last_import.taken_at)})`
        : "New export found and imported");
    }
  } catch (e) { /* the next poll tries again */ }
}

// ------------------------------------------------------------ checker panel

const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function renderChecker(c) {
  checker.enabled = c.enabled;
  $("checker").hidden = !c.enabled;
  if (!c.enabled) return;
  checker.paused = c.paused;
  checker.viewportDays = c.viewport_check_days;
  checker.dwellMs = c.viewport_dwell_seconds * 1000;
  const line = {
    running: "is running",
    idle: "is idle",
    paused: "is paused",
    cooldown: `is cooling down until ${c.cooldown_until ? fmtTime(c.cooldown_until) : "later"}`,
    cap_reached: "is waiting for its daily limit to reset",
  }[c.state] || `is ${c.state}`;
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
  const outcomes = { live: "profile found", missing: "profile not found",
    rate_limited: "Instagram asked for a pause", blocked: "request looked blocked, backing off",
    error: "unclear answer, will retry" };
  $("checker-last").textContent = last ? `Last lookup: ${outcomes[last.outcome] || last.outcome}.` : "";
  $("checker-toggle").textContent = c.paused ? "Resume" : "Pause";
}

async function pollChecker() {
  if (document.visibilityState !== "visible" || $("app").hidden) return;
  try {
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
const dwellTimers = new WeakMap();
const asked = new Set();
let pendingVisible = new Set();
let flushTimer;

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
    const row = e.target;
    if (e.isIntersecting) {
      if (!checker.enabled || dwellTimers.has(row) || !isStale(row)) continue;
      dwellTimers.set(row, setTimeout(() => {
        dwellTimers.delete(row);
        const user = row.dataset.user;
        if (asked.has(user)) return;
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

function watchRows() {
  rowObserver.disconnect();
  document.querySelectorAll("#rows .row").forEach((r) => rowObserver.observe(r));
}

$("checker-toggle").addEventListener("click", () => guard(async () => {
  renderChecker(await (await postJSON(`/api/checker/${checker.paused ? "resume" : "pause"}`, {})).json());
}));
const poll = () => { pollChecker(); pollData(); };
setInterval(poll, 10000);
setInterval(refreshRelativeDates, 60000);
document.addEventListener("visibilitychange", () => { poll(); refreshRelativeDates(); });

// ------------------------------------------------------------ long-press on a picture

// On a touch screen there is no hover, so holding a finger on a picture shows the enlarged
// preview (until you lift the finger) and the browser's image menu is kept out of the way.
// A quick tap still opens the profile.
const LONG_PRESS_MS = 350;
const MOVE_LIMIT_PX = 10;
let pressTimer = null;
let pressedAvatar = null;
let pressStart = null;
let swallowClick = false;
let lastTouchAt = -Infinity;
const rowsList = $("rows");

function endPress() {
  clearTimeout(pressTimer);
  pressTimer = null;
  if (pressedAvatar) {
    pressedAvatar.classList.remove("zoom");
    pressedAvatar = null;
    setTimeout(() => { swallowClick = false; }, 700); // the click some browsers send after a hold
  }
}

rowsList.addEventListener("pointerdown", (e) => {
  if (e.pointerType !== "touch") return;
  lastTouchAt = performance.now();
  const avatar = e.target.closest(".avatar");
  if (!avatar || !avatar.querySelector("img")) return;
  pressStart = { x: e.clientX, y: e.clientY };
  clearTimeout(pressTimer);
  pressTimer = setTimeout(() => {
    pressedAvatar = avatar;
    swallowClick = true;
    avatar.classList.add("zoom");
  }, LONG_PRESS_MS);
});
rowsList.addEventListener("pointermove", (e) => {
  if (e.pointerType !== "touch" || !pressStart || pressedAvatar) return;
  // the finger is scrolling the list, not holding
  if (Math.hypot(e.clientX - pressStart.x, e.clientY - pressStart.y) > MOVE_LIMIT_PX) {
    clearTimeout(pressTimer);
    pressTimer = null;
  }
});
document.addEventListener("pointerup", endPress);
document.addEventListener("pointercancel", endPress);
// swallow the click that follows a long press, so holding a picture never opens the profile
rowsList.addEventListener("click", (e) => {
  if (swallowClick && e.target.closest(".avatar")) { e.preventDefault(); e.stopPropagation(); }
}, true);
// no image menu on a long press (right-click with a mouse is left alone)
rowsList.addEventListener("contextmenu", (e) => {
  if (e.target.closest(".avatar") && performance.now() - lastTouchAt < 2000) e.preventDefault();
});

// ------------------------------------------------------------ settings dialog

const settingsDlg = $("settings");
let settingsFields = [];

function renderSettings(fields) {
  settingsFields = fields;
  const groups = [...new Set(fields.map((f) => f.group))];
  $("settings-fields").replaceChildren(...groups.map((g) =>
    el("fieldset", { class: "settings-group" }, el("legend", {}, g),
      fields.filter((f) => f.group === g).map((f) => {
        const input = el("input", { type: "number", id: `set-${f.key}`, name: f.key, min: f.min,
          max: f.max, step: f.type === "integer" ? "1" : "any", value: f.value, required: true });
        // "Reset" only appears when the value differs from the default.
        const reset = el("button", { type: "button", class: "linkish",
          "aria-label": `Reset ${f.label} to its default`,
          onclick: () => { input.value = f.default; sync(); input.focus(); } }, "Reset");
        const sync = () => { reset.hidden = Number(input.value) === f.default; };
        input.addEventListener("input", sync);
        sync();
        return el("div", { class: "setting" },
          el("div", { class: "setting-text" },
            el("label", { for: input.id }, f.label),
            el("p", { class: "help" }, `${f.help} Default: ${f.default} ${f.unit}.`)),
          el("div", { class: "field" }, reset,
            el("div", { class: "field-input" }, input, el("span", { class: "unit" }, f.unit))));
      }))));
  $("settings-error").hidden = true;
  // Focus the first field instead of the close button when the dialog opens.
  $("settings-fields").querySelector("input").setAttribute("autofocus", "");
}

async function openSettings() {
  await guard(async () => {
    renderSettings((await getJSON("/api/settings")).fields);
    openDialog(settingsDlg);
  });
}

async function saveSettings(ev) {
  ev.preventDefault();
  const changes = {};
  for (const f of settingsFields) {
    const value = Number($(`set-${f.key}`).value);
    if (value !== f.value) changes[f.key] = value === f.default ? null : value;
  }
  const err = $("settings-error");
  try {
    if (Object.keys(changes).length) {
      const res = await api("/api/settings", { method: "PUT",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes) });
      settingsFields = (await res.json()).fields;
    }
  } catch (e) {
    err.textContent = `${e.detail || e.message} Nothing was saved.`;
    err.hidden = false;
    return;
  }
  settingsDlg.close();
  snoozeDaysCache = null;
  banner("Settings saved. They apply right away.");
  await pollChecker();
  if (state.snapshot !== null) {
    try {
      await loadSummary();
      if (state.tab !== "history") await reloadLoaded();
    } catch (e) { /* the old numbers stay until the next refresh */ }
  }
}

$("open-settings").addEventListener("click", openSettings);
$("settings-cancel").addEventListener("click", () => settingsDlg.close());
$("settings-close").addEventListener("click", () => settingsDlg.close());
$("settings-form").addEventListener("submit", saveSettings);
$("settings-reset-all").addEventListener("click", () => {
  for (const f of settingsFields) {
    const input = $(`set-${f.key}`);
    input.value = f.default;
    input.dispatchEvent(new Event("input"));
  }
});
if (location.hash === "#settings") openSettings();  // deep link, also handy for screenshots

// ------------------------------------------------------------ wiring

let searchTimer;
$("q").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.q = e.target.value; guard(loadList); }, 250);
});
$("sort").addEventListener("change", (e) => { state.sort = e.target.value; guard(loadList); });
let loadingMore = false;
async function loadMore() {
  if (loadingMore || $("more").hidden || state.tab === "history") return;
  loadingMore = true;
  await guard(() => loadList(true));
  loadingMore = false;
}
$("more").addEventListener("click", loadMore);
// Auto-load the next page when the sentinel button scrolls near the viewport.
new IntersectionObserver((entries) => {
  if (entries.some((e) => e.isIntersecting)) loadMore();
}, { rootMargin: "400px" }).observe($("more"));
$("copy").addEventListener("click", copyUsernames);
$("owner").addEventListener("change", (e) => { state.owner = e.target.value; guard(loadSnapshots); });
$("delete-snapshot").addEventListener("click", openSnapshots);
$("snapshot").addEventListener("change", (e) => { state.snapshot = Number(e.target.value); guard(loadAll); });

guard(async () => { await init(); await syncWatch(); });
