// The summary card: headline, notes and the diagram.
import { $, el } from "./dom.js";
import { fmtIso, plural, snapshotAgeDays } from "./format.js";
import { openHowto } from "./howto.js";
import { selectTab } from "./tabs.js";

// Hovering (or keyboard-focusing) a block or legend entry fades every other category.
const summaryCard = /** @type {HTMLElement} */ (document.querySelector(".summary"));
/** The block or legend entry an event came from, if any.
 * @param {Event} e
 * @returns {HTMLElement | null} */
const catOf = (e) => (e.target instanceof Element ? e.target.closest("[data-cat]") : null);
document.addEventListener("mouseover", (e) => {
  const t = catOf(e);
  if (t) summaryCard.dataset.focus = t.dataset.cat;
});
document.addEventListener("mouseout", (e) => {
  const t = catOf(e);
  if (t && !(e.relatedTarget instanceof Node && t.contains(e.relatedTarget))) delete summaryCard.dataset.focus;
});
document.addEventListener("focusin", (e) => {
  const t = catOf(e);
  if (t && e.target instanceof Element && e.target.matches(":focus-visible")) summaryCard.dataset.focus = t.dataset.cat;
});
document.addEventListener("focusout", (e) => { if (catOf(e)) delete summaryCard.dataset.focus; });

// What a partial snapshot cannot tell you, and the way to avoid it
/** @param {import("./types.js").SnapshotInfo} snap */
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

/** @param {import("./types.js").Summary} s */
export function renderSummary(s) {
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
  $("n-following").textContent = String(s.following);
  $("n-followers").textContent = String(s.followers);
  // The diagram and legend describe the whole snapshot; tab counts leave out snoozed accounts.
  const c = s.totals;
  const mutual = c.mutual;
  fillVenn(c, s.following, s.followers);
  /** @type {Record<string, string>} */
  const tabFor = { mutual: "mutual", nfb: "not_following_back", fans: "fans" };
  const legend = $("legend");
  /** @type {[string, string, number][]} */
  const entries = [["mutual", "Mutual", mutual], ["nfb", "Not following back", c.not_following_back],
    ["fans", "You don't follow back", c.fans]];
  legend.replaceChildren(...entries.map(([cls, label, n]) =>
    el("li", { "data-cat": cls }, el("button", { type: "button", onclick: () => selectTab(tabFor[cls]) },
      el("span", { class: `dot ${cls}` }), `${label} `, el("strong", {}, n)))));
}

// One shared scale for both rows: the widest of following/followers fills the diagram. The
// mutual block is a single block spanning both rows; the red and yellow blocks sit to its right.
/** @param {import("./types.js").Summary["totals"]} t @param {number} nFollowing @param {number} nFollowers */
function fillVenn(t, nFollowing, nFollowers) {
  const venn = $("venn");
  const max = Math.max(nFollowing, nFollowers);
  // Each label starts just after the end of its own row.
  $("bars").style.setProperty("--frac-top", String(max ? nFollowing / max : 0));
  $("bars").style.setProperty("--frac-bottom", String(max ? nFollowers / max : 0));
  if (!max) { venn.replaceChildren(); return; }
  const { mutual: m, not_following_back: n, fans: f } = t;
  const rest = max - m; // equals the larger of n and f
  /** @param {number} x @param {number} of */
  const pct = (x, of) => Math.round((x / of) * 1000) / 10;
  /** @param {string} cls @param {string} tab @param {number} width @param {string} text @param {string[]} [extra] */
  const block = (cls, tab, width, text, extra = []) => el("button", {
    type: "button", class: ["seg", cls, ...extra].join(" "), style: `width:${width}%`,
    "data-cat": cls, "data-tip": text, "data-tippos": cls === "fans" ? "below" : null, "aria-label": text,
    onclick: () => selectTab(tab) });
  /** @type {HTMLElement[]} */
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
