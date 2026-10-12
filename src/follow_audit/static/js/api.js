// Talking to the server.

/**
 * Fetch from the server; anything but success becomes an Error with a message to show.
 * @param {string} path
 * @param {RequestInit} [options]
 * @returns {Promise<Response>}
 */
export async function api(path, options) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (e) {
    throw new Error("Couldn't reach the server. Check that Follow Audit is still running, then reload.");
  }
  if (!res.ok) {
    /** @type {unknown} */
    let detail = "";
    try { detail = (await res.json()).detail || ""; } catch (e) { /* not JSON */ }
    /** @type {import("./types.js").ApiError} */
    const err = new Error(`The server answered ${res.status}${detail ? ": " + detail : ""}. Check the server log.`);
    err.detail = typeof detail === "string" ? detail : "";
    throw err;
  }
  return res;
}
/**
 * @param {string} path
 * @returns {Promise<any>} the parsed answer; callers say what they expect it to be
 */
export const getJSON = async (path) => (await api(path)).json();

/** The words to show for something that was thrown.
 * @param {unknown} e */
export const errorText = (e) => (e instanceof Error ? e.message : String(e));

/**
 * @param {string} path
 * @param {unknown} body
 */
export async function postJSON(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
}
