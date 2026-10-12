// Entry point: wires the page together and starts it.
import {
  copyUsernames, guard, init, loadAll, loadList, loadMore, loadSnapshots, loadSummary, reloadLoaded,
} from "./data.js";
import { $, $input, $select, $template } from "./dom.js";
import { syncWatch } from "./polling.js";
import { openProfile } from "./profile.js";
import { openSnapshots } from "./snapshots.js";
import { state } from "./state.js";
import { banner } from "./toast.js";

// modules that only wire up their own part of the page
import "./settings.js";
import "./tooltip.js";
import "./touch.js";

// The export steps live once in a <template>; the first-run card and the dialog both show them.
for (const slot of document.querySelectorAll("[data-howto]")) {
  slot.replaceChildren($template("howto-steps").content.cloneNode(true));
}

/** @type {ReturnType<typeof setTimeout> | undefined} */
let searchTimer;
$input("q").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const box = /** @type {HTMLInputElement} */ (e.target);
  searchTimer = setTimeout(() => { state.q = box.value; guard(loadList); }, 250);
});
$select("sort").addEventListener("change", (e) => {
  state.sort = /** @type {HTMLSelectElement} */ (e.target).value;
  guard(loadList);
});

$("more").addEventListener("click", loadMore);
// Auto-load the next page when the sentinel button scrolls near the viewport.
new IntersectionObserver((entries) => {
  if (entries.some((e) => e.isIntersecting)) loadMore();
}, { rootMargin: "400px" }).observe($("more"));
$("copy").addEventListener("click", copyUsernames);
$select("owner").addEventListener("change", (e) => {
  state.owner = /** @type {HTMLSelectElement} */ (e.target).value;
  guard(loadSnapshots);
});
$("delete-snapshot").addEventListener("click", openSnapshots);
$select("snapshot").addEventListener("change", (e) => {
  state.snapshot = Number(/** @type {HTMLSelectElement} */ (e.target).value);
  guard(loadAll);
});

guard(async () => { await init(); await syncWatch(); });

// A handle for the browser tests, and for poking at the page from the console
window.followAudit = { banner, loadSummary, reloadLoaded, openProfile, state };
