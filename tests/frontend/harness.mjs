// A small browser test harness with no dependencies: it starts the app on a private port with a
// temporary state folder and a data folder seeded from the synthetic fixtures, drives headless
// Chrome over the DevTools protocol, and cleans everything up. Needs Node 22 (global WebSocket).
//
//   CHROME_BIN   path of a Chrome or Chromium binary (found automatically when not set)
//   FA_TEST_CMD  command that runs the app, default: the project's .venv python, else `uv run python`
import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const FIXTURES = path.join(ROOT, "tests", "fixtures", "exports");

export const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
    srv.on("error", reject);
  });
}

export function findChrome() {
  const candidates = [
    process.env.CHROME_BIN,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ].filter(Boolean);
  const found = candidates.find((p) => fs.existsSync(p));
  if (!found) throw new Error("No Chrome found: set CHROME_BIN");
  return found;
}

function killGroup(child) {
  try { process.kill(-child.pid, "SIGTERM"); } catch (e) { /* already gone */ }
}

// ------------------------------------------------------------------ the app

function appCommand() {
  if (process.env.FA_TEST_CMD) return process.env.FA_TEST_CMD.split(" ");
  const venv = path.join(ROOT, ".venv", "bin", "python");
  return fs.existsSync(venv) ? [venv] : ["uv", "run", "python"];
}

/** Start the app. `exports` are fixture folders copied into its data folder; `files` maps a path
 *  inside the data folder to its text, for exports the fixtures don't have. */
export async function startApp({ exports = ["json_zip", "json_folder"], files = {} } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "fa-ui-"));
  const data = path.join(dir, "data");
  const state = path.join(dir, "state");
  fs.mkdirSync(data);
  for (const name of exports) fs.cpSync(path.join(FIXTURES, name), path.join(data, name), { recursive: true });
  for (const [rel, text] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(data, rel)), { recursive: true });
    fs.writeFileSync(path.join(data, rel), text);
  }
  const port = await freePort();
  const [cmd, ...args] = appCommand();
  const child = spawn(cmd, [...args, "-m", "follow_audit", "--port", String(port)], {
    cwd: ROOT,
    detached: true,
    stdio: "ignore",
    env: { ...process.env, FA_DATA_DIR: data, FA_STATE_DIR: state, FA_CHECKER_ENABLED: "false",
      FA_WATCH_INTERVAL_SECONDS: "0" },
  });
  const url = `http://localhost:${port}`;
  for (let i = 0; ; i++) {
    try { if ((await fetch(`${url}/api/health`)).ok) break; } catch (e) { /* not up yet */ }
    if (i > 120) { killGroup(child); throw new Error("the app did not start"); }
    await wait(250);
  }
  return {
    url, data, state,
    async api(p, options) { return (await fetch(url + p, options)).json(); },
    stop() { killGroup(child); fs.rmSync(dir, { recursive: true, force: true }); },
  };
}

// ------------------------------------------------------------------ the browser

export async function launchBrowser() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "fa-chrome-"));
  const port = await freePort();
  const args = ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    "--no-first-run", "--disable-gpu", "--disable-extensions", "--window-size=1000,800"];
  if (process.env.CI) args.push("--no-sandbox");
  const chrome = spawn(findChrome(), [...args, "about:blank"], { detached: true, stdio: "ignore" });
  for (let i = 0; ; i++) {
    try { if ((await fetch(`http://127.0.0.1:${port}/json/version`)).ok) break; } catch (e) { /* not up yet */ }
    if (i > 120) { killGroup(chrome); throw new Error("Chrome did not start"); }
    await wait(250);
  }
  return {
    async newPage() {
      const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
      const page = new Page(target.webSocketDebuggerUrl);
      await page.ready();
      return page;
    },
    close() {
      killGroup(chrome);
      setTimeout(() => fs.rmSync(profile, { recursive: true, force: true }), 500);
    },
  };
}

export class Page {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.id = 0;
    this.pending = new Map();
    this.errors = []; // exceptions and console errors, for the test to look at
    this.opened = new Promise((r) => this.ws.addEventListener("open", r));
    this.ws.addEventListener("message", (m) => {
      const d = JSON.parse(m.data);
      if (d.id && this.pending.has(d.id)) { this.pending.get(d.id)(d); this.pending.delete(d.id); }
      if (d.method === "Runtime.exceptionThrown") {
        this.errors.push(d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text);
      }
      if (d.method === "Runtime.consoleAPICalled" && d.params.type === "error") {
        this.errors.push("console.error: " + d.params.args.map((a) => a.value ?? a.description).join(" "));
      }
    });
  }

  async ready() {
    await this.opened;
    await this.send("Page.enable");
    await this.send("Runtime.enable");
    await this.send("Network.enable");
    await this.send("Network.setCacheDisabled", { cacheDisabled: true });
    await this.send("Emulation.setDeviceMetricsOverride", { width: 1000, height: 800, deviceScaleFactor: 1, mobile: false });
  }

  send(method, params = {}) {
    return new Promise((resolve) => {
      const id = ++this.id;
      this.pending.set(id, resolve);
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  /** Evaluate an expression in the page; promises are awaited. Throws what the page threw. */
  async eval(expression) {
    const r = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.result.exceptionDetails) {
      throw new Error(r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text);
    }
    return r.result.result.value;
  }

  /** Run something in the page without waiting for it (to observe loading states). */
  fire(expression) { return this.eval(`(() => { (${expression}); })()`); }

  async goto(url) {
    await this.send("Page.navigate", { url });
    await this.waitFor(`document.readyState === "complete"`);
  }

  /** Wait until the expression is truthy; returns its value. */
  async waitFor(expression, { timeout = 8000, message } = {}) {
    const end = Date.now() + timeout;
    let last;
    while (Date.now() < end) {
      try { last = await this.eval(expression); if (last) return last; } catch (e) { last = e.message; }
      await wait(60);
    }
    throw new Error(`Timed out waiting for: ${message || expression} (last: ${JSON.stringify(last)})`);
  }

  rect(selector) {
    return this.eval(`(() => { const e = document.querySelector(${JSON.stringify(selector)});
      if (!e) return null; const r = e.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height }; })()`);
  }

  /** A real mouse click at the middle of the element (so what covers it matters). */
  async click(selector) {
    await this.eval(`document.querySelector(${JSON.stringify(selector)})?.scrollIntoView({ block: "center" })`);
    const r = await this.waitFor(`(() => { const e = document.querySelector(${JSON.stringify(selector)});
      if (!e) return null; const r = e.getBoundingClientRect(); return r.width && r.height ? 1 : 0 })()`, { message: `${selector} visible` })
      .then(() => this.rect(selector));
    await this.clickAt(r.x, r.y);
  }

  async clickAt(x, y) {
    await this.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
    await this.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
    await this.send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
  }

  async type(selector, text) {
    await this.eval(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); e.focus(); e.select?.(); })()`);
    await this.send("Input.insertText", { text });
  }

  async press(key) {
    const code = { Escape: 27, Enter: 13 }[key];
    const text = key === "Enter" ? "\r" : undefined; // what makes Enter submit a form
    for (const type of ["keyDown", "keyUp"]) {
      await this.send("Input.dispatchKeyEvent", { type, key, windowsVirtualKeyCode: code, code: key,
        ...(type === "keyDown" && text ? { text } : {}) });
    }
  }

  /** Give the page files to import, as the file chooser would. A value is the file's text, or
   *  `{ base64 }` for bytes. */
  upload(files) {
    return this.eval(`(() => {
      const dt = new DataTransfer();
      for (const [name, v] of ${JSON.stringify(Object.entries(files))}) {
        const body = typeof v === "string" ? v : Uint8Array.from(atob(v.base64), (c) => c.charCodeAt(0));
        dt.items.add(new File([body], name));
      }
      const input = document.getElementById("file-input");
      input.files = dt.files;
      input.dispatchEvent(new Event("change"));
    })()`);
  }

  close() { this.ws.close(); }
}

// ------------------------------------------------------------------ a test's setup

/** Run `fn` with a fresh app and a page open on it. The app is torn down afterwards. */
export async function withApp(options, browser, fn) {
  const app = await startApp(options);
  const page = await browser.newPage();
  try {
    await page.goto(app.url + "/");
    await page.waitFor(`!document.getElementById("empty").hidden || (document.getElementById("headline").textContent !== ""
      && (document.querySelectorAll("#rows .row").length > 0 || document.getElementById("count").textContent === "Nothing here"))`,
      { message: "the page to load" });
    await fn({ app, page });
  } finally {
    page.close();
    app.stop();
  }
}

/** Errors the page logged, minus the ones that are expected (no favicon; avatars of accounts
 *  that were never fetched). */
export const pageErrors = (page) =>
  page.errors.filter((e) => !/favicon|Failed to load resource/.test(e));
