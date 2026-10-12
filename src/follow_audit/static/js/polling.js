// Noticing exports that the server imported from the data folder.
import { getJSON } from "./api.js";
import { pollChecker } from "./checker.js";
import { init } from "./data.js";
import { fmtIso, refreshRelativeDates } from "./format.js";
import { banner } from "./toast.js";

// New exports dropped into the data folder are imported by the server; pick them up here.
/** @type {number | null} */
let knownImports = null;

export async function syncWatch() {
  try { knownImports = (await getJSON("/api/watch")).imports; } catch (e) { /* optional */ }
}

async function pollData() {
  if (document.visibilityState !== "visible") return;
  try {
    /** @type {import("./types.js").WatchStatus} */
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

const poll = () => { pollChecker(); pollData(); };
setInterval(poll, 10000);
setInterval(refreshRelativeDates, 60000);
document.addEventListener("visibilitychange", () => { poll(); refreshRelativeDates(); });
