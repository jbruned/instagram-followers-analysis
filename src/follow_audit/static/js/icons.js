// Small icons.
import { el } from "./dom.js";

// Small icons next to the username. The markup is fixed (no account data), so innerHTML is safe.
/** @type {Record<"private" | "close" | "reach" | "verified", [string, string]>} */
const ICONS = {
  private: ['Private account',
    '<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>'],
  close: ['Close friend',
    '<circle cx="12" cy="12" r="11" fill="#1E9E57" stroke="none"/><path fill="#fff" stroke="none" transform="translate(12 12) scale(.78) translate(-12 -12)" d="M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.6L12 17.3 6.1 20.6l1.3-6.6L2.5 9.4l6.6-.8z"/>'],
  // a studio microphone: the grille bars are cut out of the capsule with a mask (__ID__ is made unique)
  reach: ['High follower ratio',
    '<mask id="__ID__"><g stroke="none"><rect width="24" height="24" fill="#fff"/>' +
    '<rect x="6" y="5.2" width="5.1" height="1.8" fill="#000"/><rect x="12.9" y="5.2" width="5.1" height="1.8" fill="#000"/>' +
    '<rect x="6" y="8.8" width="5.1" height="1.8" fill="#000"/><rect x="12.9" y="8.8" width="5.1" height="1.8" fill="#000"/></g></mask>' +
    '<rect x="7.8" y="1.4" width="8.4" height="12.9" rx="4.2" fill="currentColor" stroke="none" mask="url(#__ID__)"/>' +
    '<path d="M6 8.5V10a6 6 0 0 0 12 0V8.5M12 16.3v3.6"/>' +
    '<rect x="7" y="20.3" width="10" height="2.4" rx="1.2" fill="currentColor" stroke="none"/>'],
  verified: ['Verified account',
    '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>'],
};

let iconSeq = 0;
/**
 * @param {keyof typeof ICONS} kind
 * @param {string} [tip]
 */
export function nameIcon(kind, tip) {
  const [label, source] = ICONS[kind];
  const paths = source.replaceAll("__ID__", `icon-mask-${++iconSeq}`); // masks need ids that are unique on the page
  const span = el("span", { class: `name-icon ${kind}`, role: "img", "aria-label": tip || label, "data-tip": tip || label });
  span.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
  return span;
}

export const TRASH_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6"/></svg>';
