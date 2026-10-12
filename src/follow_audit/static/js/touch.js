// Long-press on a picture, for touch screens.
import { $, closestTo } from "./dom.js";

// ------------------------------------------------------------ long-press on a picture

// On a touch screen there is no hover, so holding a finger on a picture shows the enlarged
// preview (until you lift the finger) and the browser's image menu is kept out of the way.
// A quick tap still opens the profile.
const LONG_PRESS_MS = 350;
const MOVE_LIMIT_PX = 10;
/** @type {ReturnType<typeof setTimeout> | null} */
let pressTimer = null;
/** @type {HTMLElement | null} */
let pressedAvatar = null;
/** @type {{ x: number, y: number } | null} */
let pressStart = null;
let swallowClick = false;
let lastTouchAt = -Infinity;
const rowsList = $("rows");

function endPress() {
  if (pressTimer) clearTimeout(pressTimer);
  pressTimer = null;
  if (pressedAvatar) {
    pressedAvatar.classList.remove("zoom");
    pressedAvatar = null;
    setTimeout(() => { swallowClick = false; }, 700); // the click some browsers send after a hold
  }
}

rowsList.addEventListener("pointerdown", (e) => {
  if (e.pointerType !== "touch") return;
  lastTouchAt = performance.now();
  const avatar = closestTo(e, ".avatar");
  if (!avatar || !avatar.querySelector("img")) return;
  pressStart = { x: e.clientX, y: e.clientY };
  if (pressTimer) clearTimeout(pressTimer);
  pressTimer = setTimeout(() => {
    pressedAvatar = avatar;
    swallowClick = true;
    avatar.classList.add("zoom");
  }, LONG_PRESS_MS);
});
rowsList.addEventListener("pointermove", (e) => {
  if (e.pointerType !== "touch" || !pressStart || pressedAvatar) return;
  // the finger is scrolling the list, not holding
  if (Math.hypot(e.clientX - pressStart.x, e.clientY - pressStart.y) > MOVE_LIMIT_PX) {
    if (pressTimer) clearTimeout(pressTimer);
    pressTimer = null;
  }
});
document.addEventListener("pointerup", endPress);
document.addEventListener("pointercancel", endPress);
// swallow the click that follows a long press, so holding a picture never opens the profile
rowsList.addEventListener("click", (e) => {
  if (swallowClick && closestTo(e, ".avatar")) { e.preventDefault(); e.stopPropagation(); }
}, true);
// no image menu on a long press (right-click with a mouse is left alone)
rowsList.addEventListener("contextmenu", (e) => {
  if (closestTo(e, ".avatar") && performance.now() - lastTouchAt < 2000) e.preventDefault();
});
