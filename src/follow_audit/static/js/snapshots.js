// The manage snapshots dialog.
import { api, getJSON } from "./api.js";
import { guard, init } from "./data.js";
import { openDialog } from "./dialogs.js";
import { $, $dialog, el } from "./dom.js";
import { fmtIso } from "./format.js";
import { TRASH_SVG } from "./icons.js";
import { ownerQuery } from "./state.js";
import { banner } from "./toast.js";
import { fileInput } from "./upload.js";

export const snapsDlg = $dialog("snapshots");

export async function renderSnapshotTable() {
  /** @type {import("./types.js").Owner[]} */
  const owners = await getJSON("/api/owners");
  const groups = await Promise.all(owners.map(async (o) => ({ label: o.label || "Unknown account",
    /** @type {import("./types.js").Snapshot[]} */
    snaps: await getJSON(`/api/snapshots?${ownerQuery(o.owner)}`) })));
  $("snapshots-table").querySelector("tbody")?.replaceChildren(...groups.flatMap((g) =>
    g.snaps.map((s) => {
      const btn = el("button", { type: "button", class: "icon-btn snap-delete", "aria-label": "Delete snapshot",
        "data-tip": "Delete snapshot" });
      btn.innerHTML = TRASH_SVG;
      /** @type {ReturnType<typeof setTimeout> | null} */
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

/** @param {import("./types.js").Snapshot} s @param {HTMLButtonElement} btn */
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

export async function openSnapshots() {
  await guard(async () => {
    await renderSnapshotTable();
    openDialog(snapsDlg);
  });
}
$("snapshots-close").addEventListener("click", () => snapsDlg.close());
$("snapshots-add").addEventListener("click", () => fileInput.click());
