import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { after, before, describe, it } from "node:test";
import { launchBrowser, pageErrors, withApp } from "./harness.mjs";
import { bigExport, cutFollowers, locked, row, waitToast, waitUnlocked, waitUsers } from "./support.mjs";

const FIXTURE_ZIP = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "fixtures", "exports",
  "json_zip", "instagram-alice-2024-03-02-AAAA.zip");

let browser;
before(async () => { browser = await launchBrowser(); });
after(() => browser.close());
const app = (fn, options = {}) => withApp(options, browser, fn);

const FOLLOWERS = (...names) =>
  JSON.stringify(names.map((value) => ({ string_list_data: [{ value, timestamp: 1_700_000_000 }] })));

describe("uploading", () => {
  it("says so when an export is already imported", () => app(async ({ page }) => {
    await page.upload({ "instagram-alice-2024-03-02-AAAA.zip": { base64: fs.readFileSync(FIXTURE_ZIP).toString("base64") } });
    await waitToast(page, "Already imported, nothing new");
  }));

  it("asks which account loose files belong to, and imports them into the one picked", () => app(async ({ app: server, page }) => {
    await page.upload({ "followers_1.json": FOLLOWERS("zed") });
    await page.waitFor(`document.getElementById("account-pick").open`);
    assert.ok(await locked(page));
    const options = await page.eval(`[...document.querySelectorAll(".account-opt")].map((l) => l.innerText.replace(/\\s+/g, " ").trim())`);
    assert.match(options[0], /^A alice 2 snapshots/);
    assert.match(options.at(-1), /Add account/);
    assert.equal(await page.eval(`document.querySelector("input[name=account]:checked").value`), "alice"); // the one you view
    assert.ok(await page.eval(`document.getElementById("account-new").hidden`));
    await page.click("#account-ok");
    await waitToast(page, "Imported 1 new snapshot");
    assert.equal((await server.api("/api/snapshots?owner=alice")).length, 3);
    await waitUnlocked(page);
  }));

  it("lets you add a new account, checking the username as you type", () => app(async ({ app: server, page }) => {
    await page.upload({ "followers_1.json": FOLLOWERS("zed") });
    await page.waitFor(`document.getElementById("account-pick").open`);
    await page.click(".account-opt:last-child");
    await page.waitFor(`!document.getElementById("account-new").hidden`);
    assert.ok(await page.eval(`document.getElementById("account-ok").disabled`)); // nothing typed yet
    await page.type("#account-name", "bad name!");
    await page.waitFor(`!document.getElementById("account-error").hidden`);
    assert.match(await page.eval(`document.getElementById("account-error").textContent`), /1 to 30 letters/);
    assert.ok(await page.eval(`document.getElementById("account-ok").disabled`));
    await page.type("#account-name", "@Bob_9");
    await page.waitFor(`!document.getElementById("account-ok").disabled`);
    assert.ok(await page.eval(`document.getElementById("account-error").hidden`));
    await page.press("Enter");
    await waitToast(page, "Imported 1 new snapshot");
    assert.deepEqual((await server.api("/api/owners")).map((o) => o.owner).sort(), ["alice", "bob_9"]);
    assert.ok(await page.eval(`!document.getElementById("owner-wrap").hidden`)); // two accounts: the picker shows
  }));

  it("imports nothing when the account question is dismissed", () => app(async ({ app: server, page }) => {
    await page.upload({ "followers_1.json": FOLLOWERS("zed") });
    await page.waitFor(`document.getElementById("account-pick").open`);
    await page.click("#account-cancel");
    await page.waitFor(`!document.getElementById("account-pick").open`);
    await waitToast(page, "Nothing was imported");
    assert.equal((await server.api("/api/snapshots?owner=alice")).length, 2);
  }));

  it("holds back a followers list cut to a date range until you say to import it anyway", () => app(async ({ app: server, page }) => {
    await page.upload({ "followers_1.json": cutFollowers() });
    await page.waitFor(`document.getElementById("account-pick").open`);
    await page.click("#account-ok"); // the only account, bob
    await page.waitFor(`document.getElementById("ask").open && /Partial export/.test(document.getElementById("ask-title").textContent)`);
    const text = await page.eval(`document.getElementById("ask-text").textContent`);
    assert.match(text, /can't detect if someone has unfollowed you since the previous snapshot/);
    assert.match(text, /All time/);
    assert.equal((await server.api("/api/snapshots?owner=bob")).length, 1); // not imported yet
    await page.click("#ask-actions button:nth-child(2)"); // Import anyway
    await waitToast(page, "Imported 1 new snapshot");
    const snaps = await server.api("/api/snapshots?owner=bob");
    assert.equal(snaps.length, 2);
    assert.ok(snaps[0].partial);
    await page.waitFor(`!document.getElementById("partial-note").hidden`);
    assert.match(await page.eval(`document.getElementById("partial-note").textContent`), /Partial export/);
    assert.match(await page.eval(`document.getElementById("snapshot").selectedOptions[0].textContent`), /partial, latest/);
  }, { exports: [], files: bigExport("bob") }));

  it("keeps the timeline's last stretch visible when the newest snapshot is from today", () => app(async ({ page }) => {
    await page.upload({ "followers_1.json": FOLLOWERS("zoe", "erin") });
    await page.waitFor(`document.getElementById("account-pick").open`);
    await page.click("#account-ok");
    await waitToast(page, "Imported 1 new snapshot");
    await page.click("#tab-fans");
    await waitUsers(page, ["erin", "zoe"]);
    await page.click(`${row("zoe")} .avatar`);
    await page.waitFor(`document.getElementById("profile").open && document.querySelectorAll(".tl-seg").length > 0`);
    const last = await page.eval(`(() => { const s = [...document.querySelectorAll(".tl-seg")].at(-1);
      return { tip: s.dataset.tip, width: parseFloat(s.style.width) }; })()`);
    assert.match(last.tip, /Today$/);
    assert.ok(last.width > 0, `the last stretch has a width (${last.width}%)`);
    assert.equal(await page.eval(`document.getElementById("profile").open`), true);
    assert.deepEqual(pageErrors(page), []);
  }));
});
