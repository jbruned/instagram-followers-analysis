// Decisions about an account: ignore, snooze, flag, and undoing them.
import { getJSON, postJSON } from "./api.js";
import { guard, loadSummary, reloadLoaded } from "./data.js";
import { ask } from "./dialogs.js";
import { plural } from "./format.js";
import { groupOf, ownerQuery, state } from "./state.js";
import { banner } from "./toast.js";

// Ignore (for good), snooze (ignore for a while) and flag are one decision at a time: choosing
// one replaces the others. The account may disappear from this list (hidden unless ticked under
// sub-tab), so the rows on screen are reloaded in place and the counts refreshed.
// What the account was set to before, so a click on "click to undo" puts it back.
/** @param {import("./types.js").Row} r
 * @returns {import("./types.js").Decision} */
function decisionOf(r) {
  return r.ignored ? "ignored" : r.snoozed_until ? "snoozed" : r.flag || "none";
}

/**
 * @param {import("./types.js").Row} r
 * @param {import("./types.js").Decision} decision
 * @param {string} outcome  what to say happened
 * @param {{ undoable?: boolean }} [options]
 */
export async function decide(r, decision, outcome, { undoable = false } = {}) {
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

/** @type {number | null} */
let snoozeDaysCache = null;
// called when the settings are saved
export function forgetSnoozeDays() { snoozeDaysCache = null; }
async function snoozeDays() {
  if (snoozeDaysCache === null) {
    try {
      /** @type {import("./types.js").SettingField[]} */
      const fields = (await getJSON("/api/settings")).fields;
      snoozeDaysCache = fields.find((f) => f.key === "snooze_days")?.value ?? null;
    } catch (e) { /* the default is used until the settings can be read */ }
  }
  return snoozeDaysCache || 30;
}

// What you are being asked depends on the list the account is in.
function ignoreQuestion() {
  /** @type {Record<string, string>} */
  const wording = { not_following_back: "this account not following you back",
    fans: "not following this account back" };
  const what = wording[groupOf(state.tab)?.id ?? ""] || "this account as it is";
  return `Are you okay with ${what}, or do you just want to snooze it and decide later?`;
}

/** @param {import("./types.js").Row} r */
export async function askIgnore(r) {
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
/** @param {string} kind @param {boolean} [set] */
function seenFlagNote(kind, set) {
  try {
    if (set) localStorage.setItem(`fa-flag-note-${kind}`, "1");
    return localStorage.getItem(`fa-flag-note-${kind}`) === "1";
  } catch (e) { return false; }
}
/** @param {import("./types.js").Row} r @param {import("./types.js").Flag} kind */
export async function flagAccount(r, kind) {
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
