// Opens one preview page in headless Chromium and holds it open until Sage closes it or `seconds`
// run out (#707). It judges nothing: the page's own reportRuntimeError posts the ack and any crash
// to Sage, as it does in the Workbench. Spawned and killed by page_check.py.
//
// After the page loads it opens each screen the app can switch to once — every top-level tab, and
// every button in its navigation (#722) — so a screen mounted only when it is chosen renders and
// runs its queries where reportRuntimeError can see them (#709), then prints `done` on stdout. A
// button outside navigation is never clicked: it can call a model or write data.
// A screen whose tab or nav button is disabled until something is chosen is reached by choosing (#764):
// while one waits, the walk clicks the first table row of the screen it is on, once, in a cell with no
// control in it — a row click selects, it does not write — and then opens what that enabled.
// Each screen it sees, the first one included, is printed as one JSON line before `done` (#750):
// its label, its visible text pieces, and how many charts and tables it drew. Sage keeps only the
// pieces the app's own code spells, so no row a query returned reaches the plan review.
// At most SETTLE_MS + MAX_SCREENS * (CLICK_MS + SETTLE_MS); test_the_screen_walk_fits_inside_the_check_budget
// holds it inside Sage's wait.
//
// usage: node page_check.mjs <url> <chromium executable> <seconds>
import { chromium } from "playwright-core";

const MAX_SCREENS = 8;
const CLICK_MS = 1000;
const SETTLE_MS = 1000;
const TEXTS_MAX = 200;
const TEXT_MAX = 120;
const NAV = ":is(nav, [role=navigation], [role=tablist])";
const SCREENS = `[role=tab], ${NAV} :is(button, [role=button])`;
const WAITING = `:is(${SCREENS}):is([aria-disabled=true], :disabled)`;
const CONTROL = "a, button, input, select, textarea, label, [role=button], [role=checkbox], [role=switch], [role=link], [contenteditable]";
const SELECTED = `:is(${SCREENS}):is([aria-selected=true], [aria-current]:not([aria-current=false]))`;

const [url, executablePath, seconds] = process.argv.slice(2);
const budget = Number(seconds) * 1000;
const browser = await chromium.launch({ executablePath, headless: true, handleSIGTERM: false,
                                        handleSIGINT: false, handleSIGHUP: false });
const done = () => browser.close().catch(() => {}).finally(() => process.exit(0));
process.on("SIGTERM", done);
setTimeout(done, budget);

// The page's own reads still in flight, so a screen read before its data arrived says so.
let inflight = 0;
const settled = (request) => {
  if (["fetch", "xhr"].includes(request.resourceType())) inflight -= 1;
};

// A chart is a visible svg or canvas big enough to be one: an icon is an svg too, and an empty
// state's picture is not a chart.
async function report(page, label) {
  const seen = await page.evaluate(([most, max]) => {
    const shown = (el) => el.checkVisibility();
    const charts = [...document.querySelectorAll("svg, canvas")].filter((el) => {
      const box = el.getBoundingClientRect();
      return shown(el) && box.width >= 100 && box.height >= 60
        && !el.parentElement?.closest("svg, .ant-empty, .ant-result");
    });
    const texts = new Set();
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode() && texts.size < most) {
      const text = walker.currentNode.nodeValue.replace(/\s+/g, " ").trim();
      const parent = walker.currentNode.parentElement;
      if (text && !parent.closest("script, style") && shown(parent)) texts.add(text.slice(0, max));
    }
    return {
      texts: [...texts],
      charts: charts.length,
      tables: [...document.querySelectorAll("table")].filter(shown).length,
      busy: [...document.querySelectorAll(".ant-spin-spinning, [aria-busy=true]")].some(shown),
    };
  }, [TEXTS_MAX, TEXT_MAX]).catch(() => null);
  if (seen === null) return;
  const { busy, ...rest } = seen;
  console.log(JSON.stringify({ screen: label, ...rest, loading: busy || inflight > 0 }));
}

async function shownLabel(page) {
  const shown = await page.$(SELECTED);
  return shown ? (await shown.innerText().catch(() => "")).trim().slice(0, 80) : "";
}

// The first visible table cell holding no control, while a screen waits on a choice.
async function rowCell(page) {
  if (!(await page.$(WAITING))) return null;
  const cell = await page.evaluateHandle((control) => [...document.querySelectorAll(
    "tbody tr:not([aria-hidden=true]) > td")].find((el) => el.checkVisibility()
      && !el.closest(`${control}, .ant-table-placeholder`) && !el.querySelector(control)) || null,
  CONTROL).catch(() => null);
  return cell?.asElement() || null;
}

// A crash replaces the app and a navigation replaces the document; either detaches the controls,
// and the walk stops there. A control that is a link, or is the screen already showing, is skipped.
async function openScreens(page) {
  const controls = await page.$$(`:is(${SCREENS}):not([aria-selected=true]):not([aria-disabled=true]):not(:disabled)`);
  let opened = 0;
  let chosen = false;
  for (;;) {
    if (opened === MAX_SCREENS) break;
    const cell = chosen ? null : await rowCell(page);
    if (!cell && !controls.length) break;
    chosen ||= Boolean(cell);
    const waiting = cell ? await page.$$(WAITING) : [];
    const control = cell || controls.shift();
    const [state, label] = await control.evaluate((el) => [!el.isConnected ? "gone"
      : el.closest("a[href]") || !el.checkVisibility() || el.getAttribute("aria-selected") === "true"
        || (el.getAttribute("aria-current") || "false") !== "false" ? "skip" : "open",
      (el.innerText || "").trim().slice(0, 80)]).catch(() => ["gone", ""]);
    if (state === "gone") break;
    if (state === "skip") continue;
    opened += 1;
    await control.click({ timeout: CLICK_MS, noWaitAfter: true }).catch(() => {});
    await page.waitForTimeout(SETTLE_MS);
    await report(page, cell ? await shownLabel(page) : label);
    for (const enabled of waiting.reverse()) {
      if (await enabled.evaluate((el) => el.isConnected && !el.matches(":disabled")
          && el.getAttribute("aria-disabled") !== "true").catch(() => false)) controls.unshift(enabled);
    }
  }
}

try {
  const page = await browser.newPage();
  page.on("request", (request) => {
    if (["fetch", "xhr"].includes(request.resourceType())) inflight += 1;
  });
  page.on("requestfinished", settled);
  page.on("requestfailed", settled);
  await page.goto(url, { waitUntil: "load", timeout: budget });
  await page.waitForTimeout(SETTLE_MS);
  await report(page, await shownLabel(page));
  await openScreens(page);
} catch (error) {
  console.error(String(error?.message || error));
}
console.log("done");
