// Tiny DOM helpers.

/**
 * The element with this id, which has to be of this kind (the ids are the ones in index.html).
 * @template {HTMLElement} T
 * @param {string} id
 * @param {new () => T} type
 * @returns {T}
 */
function byId(id, type) {
  const found = document.getElementById(id);
  if (!(found instanceof type)) throw new Error(`#${id} is missing from the page or is not a ${type.name}`);
  return found;
}
/** @param {string} id */
export const $ = (id) => byId(id, HTMLElement);
/** @param {string} id */
export const $dialog = (id) => byId(id, HTMLDialogElement);
/** @param {string} id */
export const $input = (id) => byId(id, HTMLInputElement);
/** @param {string} id */
export const $select = (id) => byId(id, HTMLSelectElement);
/** @param {string} id */
export const $button = (id) => byId(id, HTMLButtonElement);
/** @param {string} id */
export const $form = (id) => byId(id, HTMLFormElement);
/** @param {string} id */
export const $template = (id) => byId(id, HTMLTemplateElement);
/** @param {string} id */
export const $link = (id) => byId(id, HTMLAnchorElement);

/**
 * Make an element. Properties starting with "on" are event listeners; `class` sets the class;
 * a false, null or undefined property is left out; the rest are attributes.
 * @template {keyof HTMLElementTagNameMap} K
 * @param {K} tag
 * @param {Record<string, any>} [props]
 * @param {...import("./types.js").Child} children
 * @returns {HTMLElementTagNameMap[K]}
 */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

/** The closest element, from the one an event came from, that matches a selector.
 * @param {Event} e
 * @param {string} selector
 * @returns {HTMLElement | null} */
export const closestTo = (e, selector) => (e.target instanceof Element ? e.target.closest(selector) : null);

/** The items that are not null, undefined or false.
 * @template T
 * @param {(T | null | undefined | false)[]} list
 * @returns {T[]}
 */
export const present = (list) => /** @type {T[]} */ (list.filter(Boolean));

/**
 * @param {string} name
 * @param {Record<string, any>} [extra]
 */
export function profileLink(name, extra = {}) {
  return el("a", { href: `https://www.instagram.com/${encodeURIComponent(name)}/`, target: "_blank",
    rel: "noopener noreferrer", ...extra }, name);
}
