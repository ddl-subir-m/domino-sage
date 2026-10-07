// Model prose, drawn as formatted text (#681).
//
// A model writes markdown — `## Deal brief`, `**at risk**`, lists. In a text node that reads as raw
// markup; in innerHTML unsanitised it can carry a script, because marked passes HTML through. So
// this parses with marked and sanitises with DOMPurify (both in `./vendor/`), and app code never
// sets innerHTML itself. When there is no DOM to sanitise in (DOMPurify then cleans nothing), the
// text is drawn as plain text instead.
//
// Sage owns this file and `./vendor/`. Do not edit them.
import { createElement } from "react";
import { marked } from "./vendor/marked.esm.js";
import DOMPurify from "./vendor/purify.es.mjs";

// HTML for the text, or null when it cannot be made safe here.
function parse(text: string): string | null {
  if (!DOMPurify.isSupported || typeof text !== "string") return null;
  try {
    return DOMPurify.sanitize(marked.parse(text, { async: false }) as string);
  } catch {
    return null;
  }
}

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** Safe HTML for `text`: sanitised markdown, or the text itself escaped when it cannot be. */
export function renderMarkdown(text: string): string {
  const html = parse(text);
  if (html !== null) return html;
  const escaped = String(text ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);
  return `<div style="white-space: pre-wrap">${escaped}</div>`;
}

/** `<Markdown text={answer} />`: model prose as formatted text. */
export function Markdown({ text }: { text: string }) {
  const html = parse(text);
  if (html === null) return createElement("div", { style: { whiteSpace: "pre-wrap" } }, text);
  return createElement("div", { className: "sage-markdown", dangerouslySetInnerHTML: { __html: html } });
}
