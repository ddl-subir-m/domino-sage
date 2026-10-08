// The demo harness's post-build probe (#715): the stand-in for "a person would have sent a fix
// prompt". Opens a preview headless, clicks every top-level tab, and prints one JSON report:
//
//   {"tabs": [{"tab", "runtimeErrors", "crashScreen", "missingColumns", "invisibleCharts",
//              "blankFirstColumnTables", "clickFailed"?}], "error"?}
//
// usage: node probe.mjs <url> <chromium executable> <settle ms>
//
// The rules, kept simple on purpose:
//   - A top-level tab is a [role=tab] with no [role=tabpanel] above it. A tab inside a tab is not
//     clicked: it is part of the page its parent shows.
//   - After the load and after each click, the page has settled when no request has been in flight
//     for <settle ms> (capped at 20 s), which also lets a chart finish animating.
//   - runtimeErrors: `pageerror` events plus console errors, counted against the tab clicked last
//     (or "(load)", reported only when non-zero). A failed resource load logs a console error too.
//   - crashScreen: the template's error boundary card ("The app crashed while rendering") is shown.
//   - missingColumns: the template's `query <name> has no column '<key>'` reports, as name.key. The
//     probe answers every report to preview/runtime-error, preview/ack and preview/data-error
//     itself, so nothing it sees reaches Sage or starts a repair.
//   - invisibleCharts: a visible Highcharts chart whose visible series have points, and none of
//     whose marks is painted: a point's graphic needs a fill that is not none, transparent or zero
//     opacity and a non-zero width and height; a series line needs such a stroke and a length.
//   - blankFirstColumnTables: a visible <table> with body rows, whose first data cell (skipping
//     antd's selection and expand columns) is empty on every row. antd's placeholder and measure
//     rows are not rows.
// Python runs this with a hard timeout and stops its process group, so it sets no timer of its own.
import { chromium } from "playwright-core";

const [url, executablePath, settleArg] = process.argv.slice(2);
const settleMs = Number(settleArg) || 1500;
const tabs = [];
const entry = (tab) => ({ tab, runtimeErrors: 0, crashScreen: false, missingColumns: [],
                          invisibleCharts: 0, blankFirstColumnTables: 0 });
let current = entry("(load)");
let inflight = 0;
let report = { tabs };

const browser = await chromium.launch({ executablePath, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on("pageerror", () => { current.runtimeErrors += 1; });
  page.on("console", (message) => { if (message.type() === "error") current.runtimeErrors += 1; });
  page.on("request", () => { inflight += 1; });
  page.on("requestfinished", () => { inflight -= 1; });
  page.on("requestfailed", () => { inflight -= 1; });
  await page.route("**/preview/runtime-error", (route) => {
    let message = "";
    try {
      message = String(JSON.parse(route.request().postData() || "{}").message || "");
    } catch {
      // not a report this probe can read
    }
    const missing = message.match(/^query (\S+) has no column '([^']*)'/);
    if (missing) current.missingColumns.push(`${missing[1]}.${missing[2]}`);
    return route.fulfill({ status: 204 });
  });
  await page.route(/\/preview\/(ack|data-error)(\?|$)/, (route) => route.fulfill({ status: 204 }));

  const settle = async () => {
    const end = Date.now() + 20000;
    let quiet = Date.now();
    while (Date.now() < end) {
      await page.waitForTimeout(100);
      if (inflight > 0) quiet = Date.now();
      else if (Date.now() - quiet >= settleMs) return;
    }
  };

  const markTabs = () => {
    const top = [...document.querySelectorAll('[role="tab"]')]
      .filter((el) => !el.parentElement || !el.parentElement.closest('[role="tabpanel"]'));
    top.forEach((el, i) => el.setAttribute("data-probe-tab", String(i)));
    return top.map((el) => (el.textContent || "").trim().slice(0, 60));
  };

  const scan = () => {
    const visible = (el) => !!el && el.getClientRects().length > 0
      && getComputedStyle(el).visibility !== "hidden";
    const painted = (el, prop) => {
      const style = getComputedStyle(el);
      const value = style[prop];
      const alpha = Number(style[prop === "fill" ? "fillOpacity" : "strokeOpacity"]);
      return !!value && value !== "none" && value !== "transparent"
        && !/rgba\([^)]*,\s*0\)$/.test(value) && Number(style.opacity) !== 0 && alpha !== 0;
    };
    const box = (el) => (el.getBBox ? el.getBBox() : el.getBoundingClientRect());
    const crashScreen = [...document.querySelectorAll("h2")]
      .some((h) => visible(h) && /The app crashed while rendering/.test(h.textContent || ""));
    let invisibleCharts = 0;
    for (const chart of (window.Highcharts && window.Highcharts.charts) || []) {
      if (!chart || !visible(chart.renderTo)) continue;
      const series = (chart.series || []).filter((s) => s.visible !== false);
      if (!series.some((s) => (s.points || []).length)) continue;
      const marked = series.some((s) =>
        (s.points || []).some((p) => {
          const el = p && p.graphic && p.graphic.element;
          if (!el || !painted(el, "fill")) return false;
          const b = box(el);
          return b.width > 0 && b.height > 0;
        })
        || (() => {
          const el = s.graph && s.graph.element;
          if (!el || !painted(el, "stroke")) return false;
          const b = box(el);
          return b.width > 0 || b.height > 0;
        })());
      if (!marked) invisibleCharts += 1;
    }
    let blankFirstColumnTables = 0;
    for (const table of document.querySelectorAll("table")) {
      if (!visible(table)) continue;
      const rows = [...table.querySelectorAll("tbody > tr")].filter((tr) => visible(tr)
        && !tr.matches(".ant-table-placeholder, .ant-table-measure-row, [aria-hidden='true']"));
      const firsts = rows.map((tr) => [...tr.children].find((td) => td.tagName === "TD"
        && !td.matches(".ant-table-selection-column, .ant-table-row-expand-icon-cell")));
      if (firsts.length && firsts.every((td) => !td || !(td.textContent || "").trim())) {
        blankFirstColumnTables += 1;
      }
    }
    return { crashScreen, invisibleCharts, blankFirstColumnTables };
  };

  await page.goto(url, { waitUntil: "load", timeout: 60000 });
  await settle();
  const labels = await page.evaluate(markTabs);
  if (current.runtimeErrors || current.missingColumns.length) tabs.push(current);
  if (!labels.length) {
    current = entry("(page)");
    Object.assign(current, await page.evaluate(scan));
    tabs.push(current);
  }
  for (let i = 0; i < labels.length; i += 1) {
    current = entry(labels[i]);
    tabs.push(current);
    await page.evaluate(markTabs);
    try {
      await page.click(`[data-probe-tab="${i}"]`, { timeout: 5000 });
    } catch {
      current.clickFailed = true;
    }
    await settle();
    Object.assign(current, await page.evaluate(scan));
  }
} catch (error) {
  report = { tabs, error: String((error && error.message) || error).slice(0, 300) };
} finally {
  await browser.close().catch(() => {});
}
console.log(JSON.stringify(report));
