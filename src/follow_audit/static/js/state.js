// Shared state: the tabs, what is selected, and the query strings that describe it.

/**
 * @typedef {object} SubTab
 * @property {string} id  the name of the list the server returns
 * @property {string} label
 * @property {boolean} [subset]  already inside another sub-tab, so not counted twice
 * @property {string} [help]
 *
 * @typedef {object} Tab
 * @property {string} id
 * @property {string} label
 * @property {SubTab[]} subs
 * @property {string[]} [requires]  shown when the export carries any of these lists
 * @property {number} [minSnapshots]
 */

export const PAGE = 150;
// Tabs and their sub-tabs. A sub-tab id is the name of the list the server returns. The number
// on a tab is the sum of its sub-tabs (except one that only repeats part of another).
/** @type {Tab[]} */
export const TABS = [
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
/** @param {string} id */
export const isUnavailableList = (id) => id.endsWith("unavailable");
/** The tab a list belongs to.
 * @param {string} listId */
export const groupOf = (listId) => TABS.find((t) => t.subs.some((s) => s.id === listId));
/** @param {string} listId @returns {SubTab} */
export const subOf = (listId) => {
  const sub = groupOf(listId)?.subs.find((s) => s.id === listId);
  if (!sub) throw new Error(`unknown list ${listId}`);
  return sub;
};
/** @type {[string, string][]} */
export const EVENTS = [
  ["lost_follower", "Lost followers"],
  ["new_follower", "New followers"],
  ["you_unfollowed", "You unfollowed"],
  ["you_followed", "You followed"],
  ["refollow_follower", "Followers who unfollowed and followed again"],
  ["refollow_following", "Accounts you unfollowed and followed again"],
  ["renamed", "Probably renamed accounts"],
];

/** What the page knows about the account checker.
 * @type {{ enabled: boolean, paused: boolean, checks_done: number | null, viewportDays?: number, dwellMs?: number }} */
export const checker = { enabled: false, paused: false, checks_done: null };
// The selected tab (the id of its sub-tab) lives in the URL hash, so F5 and links keep it.
/** @returns {string | null} */
export function tabFromHash() {
  let id = "";
  try { id = decodeURIComponent(location.hash.slice(1)); } catch (e) { /* malformed hash */ }
  return groupOf(id) ? id : null;
}
/**
 * @type {{
 *   owner: string | null, snapshot: number | null, tab: string, q: string, sort: string,
 *   offset: number, total: number, summary: import("./types.js").Summary | null,
 *   summarySig: string, owners: import("./types.js").Owner[]
 * }}
 */
export const state = { owner: null, snapshot: null, tab: tabFromHash() || "not_following_back", q: "", sort: "newest",
  offset: 0, total: 0, summary: null, summarySig: "", owners: [] };

/** The summary of what is on screen; only called once it has loaded.
 * @returns {import("./types.js").Summary} */
export function currentSummary() {
  if (!state.summary) throw new Error("the summary has not loaded yet");
  return state.summary;
}

// "owner=..." for the endpoints that only need the account
/** @param {string | null} [owner] */
export function ownerQuery(owner = state.owner) {
  return owner !== null ? new URLSearchParams({ owner }).toString() : "";
}

/** The query string that says which account and snapshot the page is showing.
 * @param {Record<string, string | number>} [extra] */
export function qs(extra = {}) {
  const p = new URLSearchParams();
  if (state.owner !== null) p.set("owner", state.owner);
  if (state.snapshot !== null) p.set("snapshot", String(state.snapshot));
  for (const [k, v] of Object.entries(extra)) p.set(k, String(v));
  return p.toString();
}

/** @param {Record<string, string | number>} [extra] */
export function listQuery(extra = {}) {
  return qs({ q: state.q, sort: state.sort, ...extra });
}
