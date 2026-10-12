// The settings dialog.
import { forgetSnoozeDays } from "./actions.js";
import { api, getJSON } from "./api.js";
import { pollChecker } from "./checker.js";
import { guard, loadSummary, reloadLoaded } from "./data.js";
import { openDialog } from "./dialogs.js";
import { $, $dialog, $form, $input, el } from "./dom.js";
import { state } from "./state.js";
import { banner } from "./toast.js";

// ------------------------------------------------------------ settings dialog

const settingsDlg = $dialog("settings");
/** @type {import("./types.js").SettingField[]} */
let settingsFields = [];

/** @param {import("./types.js").SettingField[]} fields */
function renderSettings(fields) {
  settingsFields = fields;
  const groups = [...new Set(fields.map((f) => f.group))];
  $("settings-fields").replaceChildren(...groups.map((g) =>
    el("fieldset", { class: "settings-group" }, el("legend", {}, g),
      fields.filter((f) => f.group === g).map((f) => {
        const input = el("input", { type: "number", id: `set-${f.key}`, name: f.key, min: f.min,
          max: f.max, step: f.type === "integer" ? "1" : "any", value: f.value, required: true });
        // "Reset" only appears when the value differs from the default.
        const reset = el("button", { type: "button", class: "linkish",
          "aria-label": `Reset ${f.label} to its default`,
          onclick: () => { input.value = String(f.default); sync(); input.focus(); } }, "Reset");
        const sync = () => { reset.hidden = Number(input.value) === f.default; };
        input.addEventListener("input", sync);
        sync();
        return el("div", { class: "setting" },
          el("div", { class: "setting-text" },
            el("label", { for: input.id }, f.label),
            el("p", { class: "help" }, `${f.help} Default: ${f.default} ${f.unit}.`)),
          el("div", { class: "field" }, reset,
            el("div", { class: "field-input" }, input, el("span", { class: "unit" }, f.unit))));
      }))));
  $("settings-error").hidden = true;
  // Focus the first field instead of the close button when the dialog opens.
  $("settings-fields").querySelector("input")?.setAttribute("autofocus", "");
}

async function openSettings() {
  await guard(async () => {
    /** @type {{ fields: import("./types.js").SettingField[] }} */
    const loaded = await getJSON("/api/settings");
    renderSettings(loaded.fields);
    openDialog(settingsDlg);
  });
}

/** @param {SubmitEvent} ev */
async function saveSettings(ev) {
  ev.preventDefault();
  /** @type {Record<string, number | null>} */
  const changes = {};
  for (const f of settingsFields) {
    const value = Number($input(`set-${f.key}`).value);
    if (value !== f.value) changes[f.key] = value === f.default ? null : value;
  }
  const err = $("settings-error");
  try {
    if (Object.keys(changes).length) {
      const res = await api("/api/settings", { method: "PUT",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes) });
      settingsFields = (await res.json()).fields;
    }
  } catch (e) {
    const failure = /** @type {import("./types.js").ApiError} */ (e);
    err.textContent = `${failure.detail || failure.message} Nothing was saved.`;
    err.hidden = false;
    return;
  }
  settingsDlg.close();
  forgetSnoozeDays();
  banner("Settings saved. They apply right away.");
  await pollChecker();
  if (state.snapshot !== null) {
    try {
      await loadSummary();
      if (state.tab !== "history") await reloadLoaded();
    } catch (e) { /* the old numbers stay until the next refresh */ }
  }
}

$("open-settings").addEventListener("click", openSettings);
$("settings-cancel").addEventListener("click", () => settingsDlg.close());
$("settings-close").addEventListener("click", () => settingsDlg.close());
$form("settings-form").addEventListener("submit", saveSettings);
$("settings-reset-all").addEventListener("click", () => {
  for (const f of settingsFields) {
    const input = $input(`set-${f.key}`);
    input.value = String(f.default);
    input.dispatchEvent(new Event("input"));
  }
});
if (location.hash === "#settings") openSettings();  // deep link, also handy for screenshots
