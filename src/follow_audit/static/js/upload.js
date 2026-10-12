// Uploading exports: the file chooser, dropping files, and the questions that can follow.
import { api } from "./api.js";
import { guard, init } from "./data.js";
import { ask, openDialog } from "./dialogs.js";
import { $, $button, $dialog, $form, $input, el } from "./dom.js";
import { fmtIso, plural } from "./format.js";
import { syncWatch } from "./polling.js";
import { renderSnapshotTable, snapsDlg } from "./snapshots.js";
import { state } from "./state.js";
import { banner } from "./toast.js";

// Upload: the header button, the first-run card and dropping files anywhere on the page.
/** @param {import("./types.js").UploadItem[]} items @param {{ force?: boolean, owner?: string }} [options] */
async function uploadFiles(items, { force = false, owner = "" } = {}) {
  if (!items.length) return;
  const form = new FormData();
  for (const { file, path } of items) form.append("files", file, path || file.name);
  $button("upload").disabled = true;
  banner("Importing…");
  /** @type {import("./types.js").PartialItem[]} */
  let partial = [];
  /** @type {{ name: string }[]} */
  let unowned = [];
  await guard(async () => {
    const params = new URLSearchParams();
    if (owner) params.set("owner", owner);
    if (force) params.set("force", "1");
    /** @type {import("./types.js").UploadResult} */
    const out = await (await api(`/api/upload?${params}`, { method: "POST", body: form })).json();
    /** @type {string[]} */
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
  $button("upload").disabled = false;
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
/** @param {{ name: string }[]} unowned
 * @returns {Promise<string | null>} */
function askAccount(unowned) {
  return new Promise((resolve) => {
    const dlg = $dialog("account-pick");
    const owners = (state.owners || []).filter((o) => o.owner);
    const what = unowned.length === 1 && unowned[0].name !== "export" ? unowned[0].name : "These files";
    $("account-text").textContent =
      `${what} ${unowned.length === 1 && what !== "These files" ? "doesn't" : "don't"} say which Instagram account ${what === "These files" ? "they belong" : "it belongs"} to.`
      + (owners.length ? " Pick the account, or add a new one." : " Enter the account's username.");
    const NEW = "+new";
    /** @param {string} value @param {import("./types.js").Child} badge @param {string} title @param {string} sub */
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
    /** @returns {HTMLInputElement | null} */
    const picked = () => dlg.querySelector("input[name=account]:checked");
    const nameIn = $input("account-name");
    const okButton = $button("account-ok");
    const err = $("account-error");
    const value = () => (picked()?.value === NEW ? nameIn.value.trim().replace(/^@/, "") : picked()?.value || "");
    /** @param {boolean} typing */
    const refresh = (typing) => {
      const isNew = picked()?.value === NEW;
      $("account-new").hidden = !isNew;
      const v = value();
      const bad = isNew && v !== "" && !ACCOUNT_NAME.test(v);
      nameIn.setAttribute("aria-invalid", bad ? "true" : "false");
      err.hidden = !bad;
      if (bad) err.textContent = "Use 1 to 30 letters, numbers, dots or underscores.";
      okButton.disabled = !(isNew ? ACCOUNT_NAME.test(v) : v !== "");
      if (isNew && !typing) nameIn.focus();
    };
    const first = owners.find((o) => o.owner === state.owner) || owners[0];
    const start = first ? first.owner : NEW;
    for (const r of /** @type {NodeListOf<HTMLInputElement>} */ (dlg.querySelectorAll("input[name=account]"))) {
      r.checked = r.value === start;
      r.addEventListener("change", () => refresh(false));
    }
    nameIn.value = "";
    nameIn.oninput = () => refresh(true);
    /** @type {string | null} */
    let answer = null;
    $form("account-form").onsubmit = (e) => {
      e.preventDefault();
      if (okButton.disabled) return;
      answer = value().toLowerCase();
      dlg.close();
    };
    $("account-cancel").onclick = () => dlg.close();
    dlg.addEventListener("close", () => resolve(answer), { once: true });
    refresh(true);
    openDialog(dlg);
    if (start === NEW) nameIn.focus();
    else picked()?.focus();
  });
}

// An export whose followers look cut to a date range cannot show who unfollowed you. It is held
// back until you say to keep it.
/** @param {import("./types.js").PartialItem[]} items */
async function askPartial(items) {
  const what = items.map((p) => {
    const lead = `${p.name === "export" ? "This" : p.name} looks like a partial export: ${p.reason}.`;
    return p.first
      ? `${lead} Its followers may be incomplete, so it can't show who unfollowed you.`
      : `${lead} This partial snapshot can't detect if someone has unfollowed you since the previous snapshot (${p.previous ? fmtIso(p.previous) : "unknown"}).`;
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
/** @param {DataTransfer} dt
 * @returns {Promise<import("./types.js").UploadItem[]>} */
async function filesOfDrop(dt) {
  const entries = /** @type {FileSystemEntry[]} */ ([...(dt.items || [])].map((i) => i.webkitGetAsEntry?.()).filter(Boolean));
  if (!entries.length) return [...dt.files].map((file) => ({ file }));
  /** @type {import("./types.js").UploadItem[]} */
  const out = [];
  /** @param {FileSystemEntry} entry @param {string} prefix */
  const walk = async (entry, prefix) => {
    if (entry.isFile) {
      const file = await new Promise((ok, no) => /** @type {FileSystemFileEntry} */ (entry).file(ok, no));
      out.push({ file, path: prefix + entry.name });
    } else if (entry.isDirectory) {
      const reader = /** @type {FileSystemDirectoryEntry} */ (entry).createReader();
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

export const fileInput = $input("file-input");
$("upload").addEventListener("click", () => fileInput.click());
$("pick").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  const items = [...(fileInput.files ?? [])].map((file) => ({ file }));
  fileInput.value = "";
  uploadFiles(items);
});

let dragDepth = 0;
/** @param {DragEvent} e */
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
/** @param {boolean} on */
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
  if (e.dataTransfer) uploadFiles(await filesOfDrop(e.dataTransfer));
});
