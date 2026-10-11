// Shared bits for the browser tests.
export const users = (page) =>
  page.eval(`[...document.querySelectorAll("#rows .row")].map((r) => r.dataset.user).sort()`);

export const waitUsers = (page, expected) =>
  page.waitFor(`JSON.stringify([...document.querySelectorAll("#rows .row")].map((r) => r.dataset.user).sort())
    === ${JSON.stringify(JSON.stringify([...expected].sort()))}`, { message: `rows ${expected}` });

export const row = (user) => `#rows .row[data-user="${user}"]`;
export const ignoreButton = (user) => `${row(user)} .actions button:first-child`;
export const flagButton = (user) => `${row(user)} .actions button:last-child`;
export const toast = (page) => page.eval(`document.getElementById("banner").hidden ? "" : document.getElementById("banner").textContent`);
export const waitToast = (page, text) =>
  page.waitFor(`!document.getElementById("banner").hidden && document.getElementById("banner").textContent.includes(${JSON.stringify(text)})`,
    { message: `a message containing "${text}"` });
export const isOpen = (page, id) => page.eval(`document.getElementById(${JSON.stringify(id)}).open`);
export const waitUnlocked = (page) =>
  page.waitFor(`!document.documentElement.classList.contains("modal-open")`, { message: "the page to unlock" });
export const locked = (page) => page.eval(`document.documentElement.classList.contains("modal-open")`);

/** An export folder for the data folder: `count` followers and following, all long ago. */
export function bigExport(owner = "bob", day = "2026-10-01", count = 30) {
  const person = (prefix, i, ts) =>
    ({ string_list_data: [{ value: `${prefix}${i}`, timestamp: ts }] });
  const list = (prefix, ts) => Array.from({ length: count }, (_, i) => person(prefix, i, ts + i));
  const dir = `instagram-${owner}-${day}-ZZZZ/connections/followers_and_following`;
  return {
    [`${dir}/followers_1.json`]: JSON.stringify(list("fan", 1_600_000_000)),
    [`${dir}/following.json`]: JSON.stringify({ relationships_following: list("fan", 1_600_000_000) }),
  };
}

/** A followers file cut to recent days: a few people, none of the 30 known ones. */
export const cutFollowers = () =>
  JSON.stringify(Array.from({ length: 3 }, (_, i) =>
    ({ string_list_data: [{ value: `new${i}`, timestamp: 1_790_000_000 + i }] })));
