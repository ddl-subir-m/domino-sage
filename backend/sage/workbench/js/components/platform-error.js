window.SW = window.SW || {};

(function () {
  const { createElement: h } = React;

  // Text Sage did not write keeps its words (ADR-0014), so an error body the platform handed back
  // is rendered exactly as it came — including its nouns, its Data Source names and any line of the
  // user's own SQL it echoes. Rewriting inside it is the response-time filter that decision
  // rejects: by the time the bytes are here, provenance is gone.
  //
  // The consequence is that one screen shows two vocabularies, and this is what makes that read as
  // attribution rather than as a half-finished rename: the borrowed half is drawn as a quotation,
  // its own marked block, with Sage's sentence outside it. It is the design system's existing split
  // — a system error human-readable with a reason and a resolution step, raw output shown as it
  // came — and every surface that draws one inline uses this one block. A toast does not: it is a
  // corner that auto-dismisses, which is the wrong placement for output somebody has to read.
  //
  // `reason` and `fix` are Sage's own copy and carry the pack's words; `body` is the platform's and
  // carries its own.
  SW.PlatformError = function PlatformError({ reason, body, fix }) {
    const said = String(body === null || body === undefined ? '' : body).trim();
    return h(
      'div',
      { className: 'sw-platform-error' },
      reason ? h('p', { className: 'sw-platform-error-say' }, reason) : null,
      // A silent platform gets no quotation: an empty box would claim it said nothing when it was
      // never asked. `pre` because the body arrives with the platform's own line breaks — and it is
      // contained on its own axis because it does not always have any, so a 500-character JSON blob
      // scrolls inside this box rather than pushing the page sideways at laptop width.
      said ? h('blockquote', { className: 'sw-passthrough' }, h('pre', null, said)) : null,
      fix ? h('p', { className: 'sw-platform-error-say is-fix' }, fix) : null
    );
  };
})();
