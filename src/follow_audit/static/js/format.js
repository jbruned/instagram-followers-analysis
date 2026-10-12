// Dates, numbers and text for people.
import { el } from "./dom.js";

/** A date from a unix time in seconds.
 * @param {number} ts */
export const fmtDate = (ts) => new Date(ts * 1000).toLocaleDateString("en-GB",
  { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
/** A date from an ISO date or time.
 * @param {string} iso */
export const fmtIso = (iso) => fmtDate(Date.parse(iso + (iso.length === 10 ? "T00:00:00Z" : "")) / 1000);
/** @param {number} n @param {string} one @param {string} many */
export const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// Relative dates: "just now", "15 hours ago", "yesterday", "2 months ago", "1 year ago", and
// "in 29 days" / "tomorrow" for the future. Exact times are shown in tooltips.
const relFormat = new Intl.RelativeTimeFormat("en", { numeric: "always" });
/** @param {number} ms a time in milliseconds since 1970 */
export function relTime(ms) {
  const diff = ms - Date.now();
  const sec = Math.abs(diff) / 1000;
  if (sec < 45) return "just now";
  const min = sec / 60, hours = min / 60, days = hours / 24;
  /** @type {number} */
  let value;
  /** @type {Intl.RelativeTimeFormatUnit} */
  let unit;
  if (min < 60) [value, unit] = [Math.max(1, Math.floor(min)), "minute"];
  else if (hours < 24) [value, unit] = [Math.floor(hours), "hour"];
  else if (days < 30) [value, unit] = [Math.floor(days), "day"];
  else if (days < 365.25) [value, unit] = [Math.max(1, Math.floor(days / 30.4375)), "month"];
  else [value, unit] = [Math.floor(days / 365.25), "year"];
  if (unit === "day" && value === 1) return diff < 0 ? "yesterday" : "tomorrow";
  return relFormat.format(diff < 0 ? -value : value, unit);
}
/** @param {number} ms */
export const fmtExact = (ms) => new Date(ms).toLocaleString("en-GB",
  { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

// "Followed 3 days ago" with the exact date on hover. The text is refreshed once a
// minute (refreshRelativeDates), so a page left open never shows stale ages.
/** @param {string} label @param {number} ms */
export function whenPart(label, ms) {
  const span = el("span", { class: "when", "data-tip": fmtExact(ms) });
  span.dataset.label = label;
  span.dataset.ms = String(ms);
  span.textContent = `${label} ${relTime(ms)}`;
  return span;
}
export function refreshRelativeDates() {
  document.querySelectorAll(".when[data-ms]").forEach((span) => {
    if (span instanceof HTMLElement) span.textContent = `${span.dataset.label} ${relTime(Number(span.dataset.ms))}`;
  });
}

/** @param {string} name */
export function colorFor(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + (ch.codePointAt(0) ?? 0)) % 360;
  return `hsl(${h} 45% 40%)`;
}

/** @param {string} takenAt an ISO date */
export function snapshotAgeDays(takenAt) {
  return Math.floor((Date.now() - Date.parse(`${takenAt}T00:00:00Z`)) / 86400000);
}

const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
/** @param {number | null | undefined} n */
export const countText = (n) => (n === null || n === undefined ? "—" : compact.format(n));

/** @param {string} iso */
export const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
