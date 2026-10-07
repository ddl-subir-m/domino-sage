// A template's markdown renderer, run from the template's own file with its vendored libraries (#681).
//
// Input on stdin: `{ "template": "fastapi-antd" | "react-vite", "text": "...",
// "purify": "vendored" | "stub" | "unsupported" | "missing", "marked": "vendored" | "missing" }`.
// "vendored" is the real library, which in Node has no DOM and so reports itself unsupported; "stub"
// stands in for a DOM-backed DOMPurify and marks what it was handed. Output: `{ "element", "html" }` — what
// `Markdown` rendered (createElement is a plain object here) and what `renderMarkdown` returned.
import fs from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import vm from 'node:vm';

const TEMPLATE = new URL('../../../template/', import.meta.url).pathname;
const { template, text, purify = 'vendored', marked = 'vendored' } = JSON.parse(fs.readFileSync(0, 'utf8'));
const createElement = (type, props, ...children) => ({ type, props: { ...props, children } });
// "unsupported" is DOMPurify's own behaviour in a browser missing what it needs: `sanitize` exists
// and returns its input unchanged.
const stub = purify === 'unsupported'
  ? { isSupported: false, sanitize: (html) => html }
  : { isSupported: true, sanitize: (html) => `[sanitized]${html}` };

let helpers;
if (template === 'fastapi-antd') {
  const sandbox = { React: { createElement }, String, Object };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  const run = (rel) => vm.runInContext(fs.readFileSync(TEMPLATE + 'fastapi-antd/static/' + rel, 'utf8'), sandbox);
  if (marked === 'vendored') run('vendor/marked.umd.js');
  if (purify === 'vendored') run('vendor/purify.min.js');
  if (purify === 'stub' || purify === 'unsupported') sandbox.DOMPurify = stub;
  run('sage/markdown.js');
  helpers = sandbox.sage;
} else {
  // The vendored files are imported as they are; only the stand-ins replace an import.
  const vendor = new URL('react-vite/src/vendor/', 'file://' + TEMPLATE).href;
  globalThis.__md = { createElement, stub };
  let source = stripTypeScriptTypes(fs.readFileSync(TEMPLATE + 'react-vite/src/Markdown.tsx', 'utf8'))
    .replace('import { createElement } from "react";', 'const { createElement } = globalThis.__md;')
    .replace('"./vendor/marked.esm.js"', JSON.stringify(vendor + 'marked.esm.js'));
  source = purify === 'stub' || purify === 'unsupported'
    ? source.replace('import DOMPurify from "./vendor/purify.es.mjs";', 'const DOMPurify = globalThis.__md.stub;')
    : source.replace('"./vendor/purify.es.mjs"', JSON.stringify(vendor + 'purify.es.mjs'));
  helpers = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
}

process.stdout.write(JSON.stringify({ element: helpers.Markdown({ text }), html: helpers.renderMarkdown(text) }));
