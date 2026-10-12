// The History tab.
import { $, el, profileLink } from "./dom.js";
import { fmtDate, fmtIso } from "./format.js";
import { EVENTS } from "./state.js";

/**
 * @param {string} kind
 * @param {import("./types.js").HistoryEvent} e
 */
function eventItem(kind, e) {
  /** @param {string} text */
  const note = (text) => el("span", { class: "muted small" }, ` · ${text}`);
  const li = el("li", {}, profileLink(e.username));
  if (e.label) li.append(note(e.label));
  if (e.previous_username) li.append(note(`was ${e.previous_username}`));
  if (e.previous_ts && e.ts) li.append(note(`${fmtDate(e.previous_ts)} → ${fmtDate(e.ts)}`));
  else if (kind !== "lost_follower" && e.ts) li.append(note(fmtDate(e.ts)));
  return li;
}

/** @param {import("./types.js").HistoryGroup} g */
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

/** @param {import("./types.js").HistoryGroup[]} groups */
export function renderHistory(groups) {
  const root = $("history");
  if (!groups.length) {
    root.replaceChildren(el("p", { class: "muted" },
      "History needs at least two exports from the same account. Upload another export."));
    return;
  }
  root.replaceChildren(...groups.map(renderGroup));
}
