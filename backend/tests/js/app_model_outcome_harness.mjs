// Actual generated helper, real HTTP, and the same consumer in Node and Chromium.
import fs from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const config = { alias: 'synthetic-model', displayName: 'Synthetic Model',
  base: input.gateway, project: 'sage-358-synthetic' };
const source = fs.readFileSync(input.helper, 'utf8')
  .replace('import { appLlmConfig } from "./appLlm.config";', `const appLlmConfig = ${JSON.stringify(config)};`)
  .replaceAll('import.meta.env.DEV', String(input.preview))
  .replaceAll('import.meta.env.BASE_URL', JSON.stringify(input.previewBase));
const compiled = stripTypeScriptTypes(source);
const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(compiled).toString('base64');

async function exercise(askModel, cases) {
  const results = {};
  for (const test of cases) {
    const controller = new AbortController();
    const row = { tokens: '', answer: null, structured: null, error: null, outcomes: [] };
    const panel = typeof document === 'undefined' ? null : document.createElement('section');
    if (panel) { panel.setAttribute('aria-label', test.name); document.body.append(panel); }
    const draw = (status) => {
      row.visibleStatus = status;
      if (panel) panel.textContent = `${test.name}: ${status} ${row.error?.message || ""} ${row.tokens}`;
    };
    draw('Incomplete');
    const terminalTimer = test.name === 'stream_done_held' ? setTimeout(() => controller.abort(), 2000) : null;
    try {
      row.answer = await askModel([{ role: 'user', content: test.name }], {
        signal: controller.signal,
        onOutcome: (outcome) => row.outcomes.push(outcome),
        ...(test.stream ? { onToken: (text) => {
          row.tokens += text;
          draw('Incomplete');
          if (test.name === 'cancelled') controller.abort();
        } } : {}),
      });
      // A consumer must never reach this with the parseable partial answer used by failure cases.
      row.structured = JSON.parse(row.answer);
      draw('Complete');
    } catch (error) {
      row.error = { name: error.name, kind: error.kind, message: error.message,
        partialText: error.partialText, reason: error.reason, evidence: error.evidence };
      draw('Incomplete');
    } finally {
      if (terminalTimer) clearTimeout(terminalTimer);
    }
    results[test.name] = row;
  }
  return results;
}

if (input.browserModule) {
  const { chromium } = await import(input.browserModule);
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto(input.pageBase);
    await page.evaluate(() => { document.cookie = 'viewer=synthetic-viewer; path=/'; });
    await page.evaluate(({ moduleUrl, exerciseSource, cases }) => {
      const button = document.createElement('button');
      button.textContent = 'Run model requests';
      button.onclick = async () => {
        const { askModel } = await import(moduleUrl);
        const run = (0, eval)(`(${exerciseSource})`);
        window.result = await run(askModel, cases);
      };
      document.body.append(button);
    }, { moduleUrl, exerciseSource: exercise.toString(), cases: input.cases });
    await page.getByRole('button', { name: 'Run model requests' }).click();
    await page.waitForFunction(() => window.result, { timeout: 30000 });
    const result = await page.evaluate(() => window.result);
    for (const [name, row] of Object.entries(result)) {
      const text = await page.getByRole('region', { name, exact: true }).textContent();
      if (!text.includes(row.visibleStatus)) throw Error(`Missing visible status for ${name}`);
      if (row.error && !text.includes(row.error.message)) throw Error(`Missing visible error for ${name}`);
    }
    console.log(JSON.stringify(result));
  } finally { await browser.close(); }
} else {
  const { askModel } = await import(moduleUrl);
  console.log(JSON.stringify(await exercise(askModel, input.cases)));
}
