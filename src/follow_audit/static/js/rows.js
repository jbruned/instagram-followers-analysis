// One account as a row of the list, and updating the rows on screen.
import { askIgnore, decide, flagAccount } from "./actions.js";
import { watchRows } from "./checker.js";
import { $, el, profileLink } from "./dom.js";
import { colorFor, fmtExact, fmtIso, relTime, whenPart } from "./format.js";
import { nameIcon } from "./icons.js";
import { openProfile } from "./profile.js";
import { checker, isUnavailableList, state } from "./state.js";

// Tag for an account flagged to follow or unfollow. While the flag is still to do it says
// "Unfollow pending"; once the latest export no longer lists the account among those you follow
// (or now lists it, for a follow) it says "Unfollowed 3 days ago", counted from when you flagged
// it. Flags made before the time was recorded just say "Unfollowed".
/** @typedef {(cls: string, text: string, tip: string) => HTMLElement} Pill */

/**
 * @param {import("./types.js").Row} r
 * @param {Pill} pill
 */
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

/** The small tags that say what is going on with an account.
 * @param {import("./types.js").Row} r
 * @returns {HTMLElement[]} */
export function statusBadges(r) {
  /** @type {Pill} */
  const pill = (cls, text, tip) => el("span", { class: `badge ${cls}`.trim(), "data-tip": tip }, text);
  /** @type {HTMLElement[]} */
  const out = [];
  if (r.ignored) {
    out.push(pill("ign", "Whitelisted", "You chose to ignore this account"));
  }
  if (r.flag) out.push(flagBadge(r, pill));
  if (r.inactive) {
    out.push(pill("inactive", "Inactive", `Last post ${r.last_post_at ? fmtIso(r.last_post_at) : ""}`));
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

/** @param {import("./types.js").Row} r */
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

/** What the high-follower-ratio icon says.
 * @param {import("./types.js").Row} r */
export function ratioTip(r) {
  const followers = r.followers_count ?? 0;
  const following = r.following_count;
  if (!following) return "Follows no one";
  const times = followers / following;
  const n = times >= 10 ? Math.round(times).toLocaleString() : times.toFixed(1);
  return `${n}x more followers than following`;
}

// A plain click on a name or picture opens the profile window; a modified click (new tab, ...)
// still goes to Instagram.
/** @param {string} username */
function openOnClick(username) {
  /** @param {MouseEvent} e */
  return (e) => {
    if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    openProfile(username);
  };
}

// A row remembers what it was drawn from, so an update can tell what changed.
/** @param {import("./types.js").Row} r */
export function makeRow(r) {
  const node = renderRow(r);
  node.dataset.sig = JSON.stringify(r);
  return node;
}

// Show these rows, touching only what changed so pictures don't flicker and a focused button
// keeps its focus while the checker updates accounts in the background.
/** @param {import("./types.js").Row[]} items */
export function patchRows(items) {
  const box = $("rows");
  const shown = /** @type {HTMLElement[]} */ ([...box.children]);
  const old = new Map(shown.map((n) => [n.dataset.user, n]));
  // keep the row already on the page when what it was drawn from is unchanged
  const wanted = items.map((r) => {
    const node = old.get(r.username);
    return node && node.dataset.sig === JSON.stringify(r) ? node : makeRow(r);
  });
  const keep = new Set(wanted);
  for (const n of shown) if (!keep.has(n)) n.remove();
  // put the rows in order, moving only those that are not already in place (a moved row would
  // lose the focus of the button inside it)
  wanted.forEach((n, i) => { if (box.children[i] !== n) box.insertBefore(n, box.children[i] || null); });
  watchRows();
}
