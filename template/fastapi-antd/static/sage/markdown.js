// Model prose, drawn as formatted text (#681).
//
// A model writes markdown — `## Deal brief`, `**at risk**`, lists. In a text node that reads as raw
// markup; in innerHTML unsanitised it can carry a script, because marked passes HTML through. So
// this parses with marked and sanitises with DOMPurify (both in `static/vendor/`), and app code
// never sets innerHTML itself. When either library is missing, or there is no DOM to sanitise in
// (DOMPurify then cleans nothing), the text is drawn as plain text instead.
//
// Sage owns this file. Do not edit it.
window.sage = window.sage || {};

(function () {
  // HTML for the text, or null when it cannot be made safe here.
  function parse(text) {
    const purify = window.DOMPurify;
    if (!window.marked || !purify || !purify.isSupported || typeof text !== "string") return null;
    try {
      return purify.sanitize(window.marked.parse(text, { async: false }));
    } catch {
      return null;
    }
  }

  const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

  /** Safe HTML for `text`: sanitised markdown, or the text itself escaped when it cannot be. */
  sage.renderMarkdown = function renderMarkdown(text) {
    const html = parse(text);
    if (html !== null) return html;
    const escaped = String(text == null ? "" : text).replace(/[&<>"']/g, (c) => ESCAPES[c]);
    return `<div style="white-space: pre-wrap">${escaped}</div>`;
  };

  /** `React.createElement(sage.Markdown, { text })`: model prose as formatted text. */
  sage.Markdown = function Markdown({ text }) {
    const html = parse(text);
    if (html === null) return React.createElement("div", { style: { whiteSpace: "pre-wrap" } }, text);
    return React.createElement("div", { className: "sage-markdown", dangerouslySetInnerHTML: { __html: html } });
  };
})();
