// The floating message at the bottom of the page.
import { $ } from "./dom.js";

// Floating message that fades out by itself, so it never pushes the page down.
/** @type {ReturnType<typeof setTimeout> | undefined} */
let toastTimer;
/** @type {ReturnType<typeof setTimeout> | undefined} */
let toastHide;
function hideToast() {
  const b = $("banner");
  clearTimeout(toastTimer);
  b.classList.remove("show");
  toastHide = setTimeout(() => { b.hidden = true; }, 220);
}
// An open modal dialog makes the rest of the page unclickable and paints over it, so while one
// is open the message lives inside it: visible above its backdrop, and still clickable.
export function placeToast() {
  const b = $("banner");
  const host = [...document.querySelectorAll("dialog[open]")].pop() || document.body;
  if (b.parentElement !== host) host.append(b);
}

// A message at the bottom of the page. With `onClick` the message itself is the button
// ("..., click to undo"), and stays a little longer.
/**
 * @param {string} text  empty hides the message
 * @param {boolean} [isError]
 * @param {(() => unknown) | null} [onClick]
 */
export function banner(text, isError = false, onClick = null) {
  const b = $("banner");
  clearTimeout(toastTimer);
  clearTimeout(toastHide);
  if (!text) { hideToast(); return; }
  b.textContent = text;
  b.className = isError ? "error" : onClick ? "action" : "";
  if (onClick) {
    b.tabIndex = 0;
    b.setAttribute("role", "button");
    const fire = () => { hideToast(); onClick(); };
    b.onclick = fire;
    b.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fire(); } };
  } else {
    b.removeAttribute("tabindex");
    b.setAttribute("role", "status");
    b.onclick = b.onkeydown = null;
  }
  b.hidden = false;
  placeToast();
  void b.offsetWidth; // start the fade from the hidden state
  b.classList.add("show");
  toastTimer = setTimeout(hideToast, isError ? 8000 : onClick ? 7000 : 4000);
}
$("banner").addEventListener("click", hideToast);
