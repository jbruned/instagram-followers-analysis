import assert from "node:assert/strict";
import { after, before, describe, it } from "node:test";
import { launchBrowser, pageErrors, withApp } from "./harness.mjs";
import { flagButton, ignoreButton, isOpen, locked, row, toast, users, waitToast, waitUnlocked, waitUsers } from "./support.mjs";

let browser;
before(async () => { browser = await launchBrowser(); });
after(() => browser.close());
const app = (fn, options = {}) => withApp(options, browser, fn);

describe("the page", () => {
  it("shows the summary, the tabs and the default list", () => app(async ({ page }) => {
    assert.match(await page.eval(`document.getElementById("headline").textContent`), /of the \d+ accounts you follow/);
    assert.ok((await page.eval(`document.querySelectorAll("#tabs button").length`)) >= 5);
    assert.deepEqual(await users(page), ["hank", "newguy"]); // you follow them, they don't follow back
    assert.deepEqual(pageErrors(page), []);
  }));

  it("switches between tabs", () => app(async ({ page }) => {
    await page.click("#tab-fans");
    await waitUsers(page, ["erin", "zoe"]);
    await page.click("#tab-mutual");
    await waitUsers(page, ["bob", "carol"]);
    await page.click("#tab-pending_sent");
    await waitUsers(page, ["ivan", "judy"]);
    assert.deepEqual(await page.eval(`[...document.querySelectorAll("#subtabs button")].map((b) => b.textContent.replace(/\\s*\\d+$/, ""))`),
      ["Sent", "Received", "Unavailable accounts"]);
  }));

  it("filters the list as you type", () => app(async ({ page }) => {
    await page.type("#q", "han");
    await waitUsers(page, ["hank"]);
    await page.type("#q", "nothing like this");
    await page.waitFor(`document.getElementById("count").textContent === "Nothing here"`);
  }));

  it("shows the History tab once there are two snapshots", () => app(async ({ page }) => {
    await page.click("#tab-history");
    await page.waitFor(`!document.getElementById("history").hidden && document.getElementById("history").children.length > 0`);
    assert.ok(await page.eval(`document.getElementById("rows").hidden`));
  }));

  it("explains how to export on the first run, when there is no data", () => app(async ({ page }) => {
    assert.ok(await page.eval(`!document.getElementById("empty").hidden && document.getElementById("app").hidden`));
    assert.equal(await page.eval(`document.getElementById("pick").textContent`), "Choose files");
    assert.ok(await page.eval(`document.querySelector("#empty [data-howto]").children.length > 0`)); // the steps
  }, { exports: [] }));

  it("warns when the newest snapshot is older than the setting allows", () => app(async ({ app: server, page }) => {
    assert.ok(await page.eval(`document.getElementById("old-note").hidden`));
    await server.api("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_snapshot_days: 1 }) });
    await page.eval(`loadSummary()`);
    await page.waitFor(`!document.getElementById("old-note").hidden`);
    assert.match(await page.eval(`document.getElementById("old-note").textContent`), /Old snapshot.*days ago/s);
    assert.ok(await isOpen(page, "howto") === false);
    await page.click("#old-note .linklike");
    await page.waitFor(`document.getElementById("howto").open`);
  }));
});

describe("dialogs", () => {
  it("lock the page while open and close with Escape or a click on the backdrop", () => app(async ({ page }) => {
    await page.click("#open-settings");
    await page.waitFor(`document.getElementById("settings").open`);
    assert.ok(await locked(page));
    await page.press("Escape");
    await page.waitFor(`!document.getElementById("settings").open`);
    await waitUnlocked(page);
    await page.click("#open-settings");
    await page.waitFor(`document.getElementById("settings").open`);
    await page.clickAt(4, 4); // the backdrop
    await page.waitFor(`!document.getElementById("settings").open`);
    await waitUnlocked(page);
  }));

  it("save settings, which apply right away", () => app(async ({ app: server, page }) => {
    await page.click("#open-settings");
    await page.waitFor(`document.getElementById("settings").open && document.getElementById("set-snooze_days")`);
    await page.type("#set-snooze_days", "12");
    await page.click("#settings-save");
    await waitToast(page, "Settings saved");
    const fields = (await server.api("/api/settings")).fields;
    assert.equal(fields.find((f) => f.key === "snooze_days").value, 12);
    assert.ok(!(await isOpen(page, "settings")));
  }));

  it("keep a message clickable above their backdrop", () => app(async ({ page }) => {
    await page.click("#delete-snapshot");
    await page.waitFor(`document.getElementById("snapshots").open`);
    await page.fire(`window.__clicks = 0, banner("Over the dialog, click to undo", false, () => { window.__clicks++; })`);
    await waitToast(page, "Over the dialog");
    assert.equal(await page.eval(`document.getElementById("banner").parentElement.tagName`), "DIALOG");
    await page.click("#banner");
    await page.waitFor(`window.__clicks === 1`);
    assert.ok(await isOpen(page, "snapshots")); // the click was the message's, not the backdrop's
    await page.click("#snapshots-close");
    await page.waitFor(`document.getElementById("banner").parentElement.tagName === "BODY"`);
  }));

  it("manage snapshots: lists them with their import time and deletes with a second click", () => app(async ({ app: server, page }) => {
    await page.click("#delete-snapshot");
    await page.waitFor(`document.querySelectorAll("#snapshots-table tbody tr").length === 2`);
    assert.deepEqual(await page.eval(`[...document.querySelectorAll("#snapshots-table th")].map((t) => t.textContent)`),
      ["Account", "Date", "Imported", "Type", ""]);
    await page.click("#snapshots-table .snap-delete");
    assert.equal(await page.eval(`document.querySelector("#snapshots-table .snap-delete").textContent`), "Sure?");
    await page.click("#snapshots-table .snap-delete");
    await page.waitFor(`document.querySelectorAll("#snapshots-table tbody tr").length === 1`);
    assert.equal((await server.api("/api/snapshots?owner=alice")).length, 1);
  }));
});

describe("account actions", () => {
  it("flagging an account to unfollow shows a message that undoes it when clicked", () => app(async ({ page }) => {
    await page.click(flagButton("hank"));
    await page.waitFor(`document.getElementById("ask").open`);
    assert.match(await page.eval(`document.getElementById("ask-title").textContent`), /Flagged for unfollow/);
    await page.click("#ask-actions button.primary"); // Got it
    await waitUsers(page, ["newguy"]);
    assert.equal(await toast(page), "hank: flagged to unfollow, click to undo");
    await page.click("#banner");
    await waitUsers(page, ["hank", "newguy"]);
    await waitToast(page, "back in your lists");
  }));

  it("ignoring an account moves it out of the pending list, and can be undone", () => app(async ({ page }) => {
    await page.click(ignoreButton("newguy"));
    await page.waitFor(`document.getElementById("ask").open`);
    assert.match(await page.eval(`document.getElementById("ask-title").textContent`), /Ignore newguy\?/);
    await page.click("#ask-actions button.primary"); // Ignore for good
    await waitUsers(page, ["hank"]);
    await waitToast(page, "newguy: ignored, click to undo");
    await page.click("#banner");
    await waitUsers(page, ["hank", "newguy"]);
  }));

  it("snoozing is also undoable", () => app(async ({ page }) => {
    await page.click(ignoreButton("hank"));
    await page.waitFor(`document.getElementById("ask").open`);
    await page.click("#ask-actions button:nth-child(2)"); // Snooze
    await waitUsers(page, ["newguy"]);
    await waitToast(page, "snoozed for");
    assert.match(await toast(page), /click to undo$/);
    await page.click("#banner");
    await waitUsers(page, ["hank", "newguy"]);
  }));

  it("the undo message can be used from the keyboard", () => app(async ({ page }) => {
    await page.click(flagButton("hank"));
    await page.waitFor(`document.getElementById("ask").open`);
    await page.click("#ask-actions button.primary");
    await page.waitFor(`!document.getElementById("ask").open && document.getElementById("banner").parentElement.tagName === "BODY"`);
    await waitToast(page, "click to undo");
    await page.eval(`document.getElementById("banner").focus()`);
    await page.press("Enter");
    await waitUsers(page, ["hank", "newguy"]);
  }));

  it("only redraws the rows that changed", () => app(async ({ app: server, page }) => {
    await page.eval(`document.querySelector(${JSON.stringify(row("hank"))}).__same = true`);
    await server.api("/api/decisions/newguy?owner=alice", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision: "ignored" }) });
    await page.eval(`reloadLoaded()`);
    await waitUsers(page, ["hank"]);
    assert.ok(await page.eval(`document.querySelector(${JSON.stringify(row("hank"))}).__same === true`),
      "the unchanged row was kept");
  }));
});

describe("the profile window", () => {
  it("shows how you are related, with a timeline that reaches today", () => app(async ({ page }) => {
    await page.click(`${row("hank")} .avatar`);
    await page.waitFor(`document.getElementById("profile").open`);
    assert.ok(await locked(page));
    const text = await page.waitFor(`document.getElementById("profile-body").textContent.includes("You followed them") && document.getElementById("profile-body").textContent`);
    assert.match(text, /hank/);
    assert.match(text, /They followed you\s*Never/);
    const tips = await page.eval(`[...document.querySelectorAll(".tl-seg")].map((s) => s.dataset.tip)`);
    assert.ok(tips.length >= 1);
    assert.match(tips.at(-1), /Today$/); // the last stretch runs to today
    assert.ok((await page.eval(`parseFloat(document.querySelector(".tl-seg:last-child").style.width)`)) > 0);
    await page.click("#profile-close");
    await page.waitFor(`!document.getElementById("profile").open`);
    await waitUnlocked(page);
  }));

  it("opens from the username link and offers the account's actions", () => app(async ({ page }) => {
    await page.click(`${row("newguy")} .uname`);
    await page.waitFor(`document.getElementById("profile").open`);
    const actions = await page.waitFor(`[...document.querySelectorAll("#profile-actions > *")].map((b) => b.textContent.trim()).join("|")`);
    assert.match(actions, /Visit profile/);
    assert.match(actions, /Ignore/);
    assert.match(actions, /Unfollow/);
  }));
});
