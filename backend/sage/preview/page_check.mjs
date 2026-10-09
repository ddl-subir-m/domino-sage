// Opens one preview page in headless Chromium and holds it open until Sage closes it or `seconds`
// run out (#707). It judges nothing: the page's own reportRuntimeError posts the ack and any crash
// to Sage, as it does in the Workbench. Spawned and killed by page_check.py.
//
// After the page loads it opens each screen the app can switch to once — every top-level tab, and
// every button in its navigation (#722) — so a screen mounted only when it is chosen renders and
// runs its queries where reportRuntimeError can see them (#709), then prints `done` on stdout. A
// button outside navigation is never clicked: it can call a model or write data.
// At most MAX_SCREENS * (CLICK_MS + SETTLE_MS); test_the_screen_walk_fits_inside_the_check_budget
// holds it inside Sage's wait.
//
// usage: node page_check.mjs <url> <chromium executable> <seconds>
import { chromium } from "playwright-core";

const MAX_SCREENS = 8;
const CLICK_MS = 1000;
const SETTLE_MS = 1000;
const NAV = ":is(nav, [role=navigation], [role=tablist])";
const SCREENS = `[role=tab], ${NAV} :is(button, [role=button])`;

const [url, executablePath, seconds] = process.argv.slice(2);
const budget = Number(seconds) * 1000;
const browser = await chromium.launch({ executablePath, headless: true, handleSIGTERM: false,
                                        handleSIGINT: false, handleSIGHUP: false });
const done = () => browser.close().catch(() => {}).finally(() => process.exit(0));
process.on("SIGTERM", done);
setTimeout(done, budget);

// A crash replaces the app and a navigation replaces the document; either detaches the controls,
// and the walk stops there. A control that is a link, or is the screen already showing, is skipped.
async function openScreens(page) {
  const controls = await page.$$(`:is(${SCREENS}):not([aria-selected=true]):not([aria-disabled=true]):not(:disabled)`);
  let opened = 0;
  for (const control of controls) {
    if (opened === MAX_SCREENS) break;
    const state = await control.evaluate((el) => !el.isConnected ? "gone"
      : el.closest("a[href]") || !el.checkVisibility()
        || (el.getAttribute("aria-current") || "false") !== "false" ? "skip" : "open").catch(() => "gone");
    if (state === "gone") break;
    if (state === "skip") continue;
    opened += 1;
    await control.click({ timeout: CLICK_MS, noWaitAfter: true }).catch(() => {});
    await page.waitForTimeout(SETTLE_MS);
  }
}

try {
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: "load", timeout: budget });
  await openScreens(page);
} catch (error) {
  console.error(String(error?.message || error));
}
console.log("done");
