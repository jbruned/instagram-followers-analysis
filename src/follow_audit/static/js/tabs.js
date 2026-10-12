// Tabs and sub-tabs.
import { guard, loadList } from "./data.js";
import { $, el } from "./dom.js";
import { currentSummary, groupOf, state, tabFromHash, TABS } from "./state.js";

function visibleTabs() {
  const summary = currentSummary();
  const present = summary.kinds_present;
  return TABS.filter((t) => (!t.requires || t.requires.some((k) => present.includes(k)))
    && (!t.minSnapshots || summary.snapshot_count >= t.minSnapshots));
}

// a "subset" sub-tab (Close friends) is already inside another one, so it is not added twice
/** @param {import("./state.js").Tab} t */
const subCount = (t) => t.subs.reduce((n, sub) => n + (sub.subset ? 0 : currentSummary().counts[sub.id]), 0);

// A tab opens on its first sub-tab that has accounts (the first one when all are empty).
/** @param {import("./state.js").Tab} t */
const firstFilled = (t) => (t.subs.find((sub) => currentSummary().counts[sub.id] > 0) || t.subs[0]).id;

export function renderTabs() {
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
      sub.label, el("span", { class: "n" }, currentSummary().counts[sub.id]))));
}

/** @param {string} id the sub-tab to show */
export async function selectTab(id) {
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
