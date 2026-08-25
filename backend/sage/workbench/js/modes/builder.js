window.SW = window.SW || {};

(function () {
  const { createElement: h } = React;

  // Build is still the existing builder HTML. The Workbench chrome around it is
  // the shell; this pane embeds that page until Build is wired through the same
  // React tree. BASE in the embedded page strips `/builder` so its /api calls
  // still hit the orchestrator.
  SW.BuildMode = function BuildMode() {
    return h('iframe', {
      className: 'sw-builder-embed',
      src: './builder',
      title: 'Build',
      style: { width: '100%', height: '100%', border: 0, display: 'block', background: '#fff' },
    });
  };
})();
