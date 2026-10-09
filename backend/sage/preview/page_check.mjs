// Opens one preview page in headless Chromium and holds it open until Sage closes it or `seconds`
// run out (#707). It judges nothing: the page's own reportRuntimeError posts the ack and any crash
// to Sage, as it does in the Workbench. Spawned and killed by page_check.py.
//
// After the page loads it opens each top-level tab once, so a pane antd mounts lazily renders and
// runs its queries where reportRuntimeError can see them (#709), then prints `done` on stdout.
// At most MAX_TABS * (CLICK_MS + SETTLE_MS); test_the_tab_walk_fits_inside_the_check_budget holds it
// inside Sage's wait.
//
// usage: node page_check.mjs <url> <chromium executable> <seconds>
import { chromium } from "playwright-core";

const MAX_TABS = 8;
const CLICK_MS = 1000;
const SETTLE_MS = 1000;

const [url, executablePath, seconds] = process.argv.slice(2);
const budget = Number(seconds) * 1000;
const browser = await chromium.launch({ executablePath, headless: true, handleSIGTERM: false,
                                        handleSIGINT: false, handleSIGHUP: false });
const done = () => browser.close().catch(() => {}).finally(() => process.exit(0));
process.on("SIGTERM", done);
setTimeout(done, budget);

// A crash replaces the app and a navigation replaces the document; either detaches the tabs, and
// the walk stops there. A tab that is a link is never clicked.
async function openTabs(page) {
  const tabs = await page.$$("[role=tab]:not([aria-selected=true]):not([aria-disabled=true])");
  let opened = 0;
  for (const tab of tabs) {
    if (opened === MAX_TABS) break;
    const state = await tab.evaluate((el) => !el.isConnected ? "gone"
      : el.closest("a[href]") || !el.checkVisibility() ? "skip" : "tab").catch(() => "gone");
    if (state === "gone") break;
    if (state === "skip") continue;
    opened += 1;
    await tab.click({ timeout: CLICK_MS, noWaitAfter: true }).catch(() => {});
    await page.waitForTimeout(SETTLE_MS);
  }
}

try {
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: "load", timeout: budget });
  await openTabs(page);
} catch (error) {
  console.error(String(error?.message || error));
}
console.log("done");
