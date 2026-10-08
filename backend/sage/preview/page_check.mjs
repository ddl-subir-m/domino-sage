// Opens one preview page in headless Chromium and holds it open until Sage closes it or `seconds`
// run out (#707). It judges nothing: the page's own reportRuntimeError posts the ack and any crash
// to Sage, as it does in the Workbench. Spawned and killed by page_check.py.
//
// usage: node page_check.mjs <url> <chromium executable> <seconds>
import { chromium } from "playwright-core";

const [url, executablePath, seconds] = process.argv.slice(2);
const budget = Number(seconds) * 1000;
const browser = await chromium.launch({ executablePath, headless: true, handleSIGTERM: false,
                                        handleSIGINT: false, handleSIGHUP: false });
const done = () => browser.close().catch(() => {}).finally(() => process.exit(0));
process.on("SIGTERM", done);
setTimeout(done, budget);
try {
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: "load", timeout: budget });
} catch (error) {
  console.error(String(error?.message || error));
}
