// One tooltip for everything with a data-tip attribute.
import { el } from "./dom.js";

// One tooltip for everything with a data-tip attribute (header icons, diagram blocks). It is
// centred on its target, kept inside the window, and its arrow points at the target. Put
// data-tippos="below" on a target to prefer showing it underneath.
const tipEl = el("div", { class: "tip", role: "tooltip" });
document.body.append(tipEl);
/** @type {ReturnType<typeof setTimeout> | undefined} */
let tipTimer;
/** @param {HTMLElement} target */
function showTip(target) {
  // A modal dialog sits above everything else on the page, so the tip must live inside it
  const host = target.closest("dialog[open]") || document.body;
  if (tipEl.parentElement !== host) host.append(tipEl);
  tipEl.textContent = target.dataset.tip ?? "";
  tipEl.style.left = "0px";
  tipEl.style.top = "0px";
  const t = target.getBoundingClientRect();
  const w = tipEl.offsetWidth;
  const h = tipEl.offsetHeight;
  const centre = t.left + t.width / 2;
  const left = Math.min(Math.max(8, centre - w / 2), window.innerWidth - w - 8);
  const fitsAbove = t.top - h - 10 >= 8;
  const fitsBelow = t.bottom + h + 10 <= window.innerHeight - 8;
  const below = target.dataset.tippos === "below" ? fitsBelow || !fitsAbove : !fitsAbove;
  tipEl.style.left = `${left}px`;
  tipEl.style.top = `${below ? t.bottom + 10 : t.top - h - 10}px`;
  tipEl.style.setProperty("--arrow-x", `${centre - left}px`);
  tipEl.classList.toggle("below", below);
  tipEl.classList.add("show");
}
function hideTip() {
  clearTimeout(tipTimer);
  tipEl.classList.remove("show");
}
/** The element with a tooltip that an event came from, if any.
 * @param {Event} e
 * @returns {HTMLElement | null} */
const tipTarget = (e) => (e.target instanceof Element ? e.target.closest("[data-tip]") : null);
document.addEventListener("mouseover", (e) => {
  const target = tipTarget(e);
  if (!target) return;
  clearTimeout(tipTimer);
  tipTimer = setTimeout(() => showTip(target), 200);
});
document.addEventListener("mouseout", (e) => { if (tipTarget(e)) hideTip(); });
// keyboard focus only: a mouse click shouldn't leave the tooltip hanging around
document.addEventListener("focusin", (e) => {
  const target = tipTarget(e);
  if (target && target.matches(":focus-visible")) showTip(target);
});
document.addEventListener("focusout", (e) => { if (tipTarget(e)) hideTip(); });
document.addEventListener("pointerdown", hideTip);
window.addEventListener("scroll", hideTip, { passive: true });
