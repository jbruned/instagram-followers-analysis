// Modal dialogs: opening and locking the page, and the small question dialog.
import { $, $dialog, el } from "./dom.js";
import { placeToast } from "./toast.js";

// An open dialog locks the page behind it. `close` fires for Cancel, Save, the X, a backdrop
// click and Escape alike, and does not bubble, hence the capture.
/** @param {HTMLDialogElement} dlg */
export function openDialog(dlg) {
  if (!dlg.open) dlg.showModal();
  document.documentElement.classList.add("modal-open");
  placeToast();
}
document.addEventListener("close", () => {
  if (!document.querySelector("dialog[open]")) document.documentElement.classList.remove("modal-open");
  placeToast(); // out of a dialog that just closed
}, true);

// A small question dialog. Resolves with the value of the button pressed, or null if it was
// dismissed (Escape, the Cancel button or a click on the backdrop).
const askDlg = $dialog("ask");
/**
 * @template T
 * @param {{ title: string, text: string, choices: { label: string, value: T, primary?: boolean }[] }} question
 * @returns {Promise<T | null>}
 */
export function ask({ title, text, choices }) {
  return new Promise((resolve) => {
    $("ask-title").textContent = title;
    $("ask-text").textContent = text;
    /** @type {T | null} */
    let answer = null;
    $("ask-actions").replaceChildren(...choices.map((c) => el("button", {
      type: "button", class: c.primary ? "primary" : "",
      onclick: () => { answer = c.value; askDlg.close(); } }, c.label)));
    askDlg.addEventListener("close", () => resolve(answer), { once: true });
    openDialog(askDlg);
  });
}
// Every dialog closes on a click on its backdrop (the dialog element itself, since the form fills it).
document.addEventListener("click", (e) => {
  if (e.target instanceof HTMLDialogElement && e.target.open) e.target.close();
});
