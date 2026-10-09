// Which of a page's fetches the fastapi-antd `reportRuntimeError.js` tags with the document's
// validation id, so the preview proxy counts them as this document's data reads (#743).
//
// Input on stdin: `{ "urls": [<what the page passes to fetch, relative to the preview base>] }`.
// Output: the subset whose request carried `X-Sage-Validation`, in input order.
import fs from 'node:fs';
import vm from 'node:vm';

const TEMPLATE = new URL('../../../template/', import.meta.url).pathname;
const { urls } = JSON.parse(fs.readFileSync(0, 'utf8'));
const origin = 'http://127.0.0.1:8080';
const base = '/preview/app_0123456789abcdef01234/';
const seen = [];
const window = {
  location: { href: `${origin}${base}?sageValidation=v1`, origin, search: '?sageValidation=v1' },
  addEventListener() {},
  fetch: async (input, init) => {
    seen.push(new Headers(init?.headers).get('X-Sage-Validation'));
    return { ok: true };
  },
};
window.sage = { base, preview: true };
const context = vm.createContext({
  window, sage: window.sage, URL, URLSearchParams, Headers, Request,
  document: { readyState: 'complete', visibilityState: 'hidden', addEventListener() {} },
  fetch: (...args) => window.fetch(...args),
});
vm.runInContext(fs.readFileSync(`${TEMPLATE}fastapi-antd/static/sage/reportRuntimeError.js`, 'utf8'),
                context);
const tagged = [];
for (const url of urls) {
  seen.length = 0;
  await window.fetch(new URL(url, `${origin}${base}`).href);
  if (seen[0] === 'v1') tagged.push(url);
}
process.stdout.write(JSON.stringify(tagged));
